from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.routes.datasets import get_dataset_storage
from app.main import app
from app.services.datasets import MAX_UPLOAD_BYTES, DatasetPreparationError, DatasetService
from app.storage.local import LocalDatasetStorage


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app.dependency_overrides[get_dataset_storage] = lambda: LocalDatasetStorage(tmp_path)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_dataset_storage, None)


def upload_csv(client: TestClient, csv_text: str, **form_data: object):
    return client.post(
        "/api/datasets",
        files={"file": ("sample.csv", csv_text.encode(), "text/csv")},
        data=form_data,
    )


def test_upload_parses_metadata_and_persists_dataset(client: TestClient, tmp_path: Path) -> None:
    response = upload_csv(
        client,
        "record_id,group,height,weight\nA-10,case,1.5,50\nB-20,control,2.5,60\n",
        label_column="group",
        row_id_column="record_id",
    )

    assert response.status_code == 201
    metadata = response.json()
    assert metadata["feature_columns"] == ["height", "weight"]
    assert metadata["feature_count"] == 2
    assert metadata["observation_count"] == 2
    assert metadata["columns"][0]["name"] == "record_id"
    assert metadata["columns"][0]["dtype"] in {"object", "str"}
    assert metadata["columns"][0]["non_missing_count"] == 2
    assert metadata["columns"][0]["unique_count"] == 2

    stored = LocalDatasetStorage(tmp_path).get(UUID(metadata["dataset_id"]))
    assert stored is not None
    assert stored.row_ids == ["A-10", "B-20"]
    assert stored.labels == ["case", "control"]
    np.testing.assert_array_equal(stored.features, [[1.5, 50], [2.5, 60]])


def test_identifier_columns_are_excluded_and_row_ids_preserved(
    client: TestClient, tmp_path: Path
) -> None:
    response = upload_csv(
        client,
        "patient,site,signal\nP-1,North,4\nP-2,South,7\n",
        identifier_columns=["patient", "site"],
        row_id_column="patient",
    )

    metadata = response.json()
    stored = LocalDatasetStorage(tmp_path).get(UUID(metadata["dataset_id"]))
    assert metadata["feature_columns"] == ["signal"]
    assert stored is not None
    assert stored.row_ids == ["P-1", "P-2"]


@pytest.mark.parametrize(
    ("csv_text", "message"),
    [
        ("x,y\n1,2\n,3\n", "missing values"),
        ("x,y\n1,2\nword,3\n", "nonnumeric values"),
        ("x,y\n1,2\ninf,3\n", "nonfinite values"),
    ],
)
def test_rejects_invalid_feature_values(
    client: TestClient, csv_text: str, message: str
) -> None:
    response = upload_csv(client, csv_text)

    assert response.status_code == 422
    assert message in response.json()["detail"]


def test_label_and_identifier_columns_are_not_numeric_features(client: TestClient) -> None:
    response = upload_csv(
        client,
        "sample_id,label,value\nS1,case,1\nS2,control,2\n",
        label_column="label",
        identifier_columns=["sample_id"],
    )

    assert response.status_code == 201
    assert response.json()["feature_columns"] == ["value"]


def test_headerless_csv_preserves_first_row_and_allows_first_column_as_label(
    client: TestClient,
    tmp_path: Path,
) -> None:
    response = upload_csv(
        client,
        "7,0.1,0.2\n3,0.3,0.4\n9,0.5,0.6\n",
        has_header="false",
        label_column="column_0",
    )

    assert response.status_code == 201
    metadata = response.json()
    assert metadata["source_row_count"] == 3
    assert metadata["observation_count"] == 3
    assert metadata["columns"][0]["name"] == "column_0"
    assert metadata["label_column"] == "column_0"
    assert metadata["feature_columns"] == ["column_1", "column_2"]

    stored = LocalDatasetStorage(tmp_path).get(UUID(metadata["dataset_id"]))
    assert stored is not None
    assert stored.row_ids == [0, 1, 2]
    assert stored.labels == [7, 3, 9]
    np.testing.assert_array_equal(stored.features, [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]])


def test_subsampling_is_deterministic_and_keeps_row_alignment(
    client: TestClient, tmp_path: Path
) -> None:
    csv_text = "row_id,label,value\n" + "".join(
        f"R{index},class-{index % 2},{index}\n" for index in range(20)
    )
    parameters = {
        "label_column": "label",
        "row_id_column": "row_id",
        "max_samples": "7",
        "random_seed": "19",
    }
    first = upload_csv(client, csv_text, **parameters).json()
    second = upload_csv(client, csv_text, **parameters).json()
    storage = LocalDatasetStorage(tmp_path)
    dataset_a = storage.get(UUID(first["dataset_id"]))
    dataset_b = storage.get(UUID(second["dataset_id"]))

    assert dataset_a is not None and dataset_b is not None
    assert first["subsampled"] is True
    assert first["observation_count"] == 7
    assert dataset_a.row_ids == dataset_b.row_ids
    assert dataset_a.labels == dataset_b.labels
    np.testing.assert_array_equal(dataset_a.features, dataset_b.features)
    assert dataset_a.row_ids == [f"R{int(value)}" for value in dataset_a.features[:, 0]]


def test_standardization_centers_and_scales_features(client: TestClient, tmp_path: Path) -> None:
    response = upload_csv(
        client,
        "a,b\n1,10\n2,20\n3,30\n",
        preprocessing="standardize",
    )

    assert response.status_code == 201
    stored = LocalDatasetStorage(tmp_path).get(UUID(response.json()["dataset_id"]))
    assert stored is not None
    np.testing.assert_allclose(stored.features.mean(axis=0), [0, 0], atol=1e-12)
    np.testing.assert_allclose(stored.features.std(axis=0), [1, 1], atol=1e-12)


def test_dataset_metadata_can_be_retrieved_and_missing_dataset_is_404(
    client: TestClient,
) -> None:
    created = upload_csv(client, "x\n1\n2\n")
    dataset_id = created.json()["dataset_id"]

    fetched = client.get(f"/api/datasets/{dataset_id}")
    missing = client.get("/api/datasets/00000000-0000-0000-0000-000000000000")

    assert fetched.status_code == 200
    assert fetched.json() == created.json()
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Dataset not found."}


def test_invalid_selection_returns_validation_error(client: TestClient) -> None:
    response = upload_csv(client, "value\n1\n2\n", label_column="unknown")

    assert response.status_code == 422
    assert "Unknown selected column" in response.json()["detail"]


def test_csv_upload_limit_is_200_mib() -> None:
    assert MAX_UPLOAD_BYTES == 200 * 1024 * 1024


def test_dataset_service_rejects_content_over_configured_upload_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.datasets.MAX_UPLOAD_BYTES", 8)

    with pytest.raises(DatasetPreparationError, match="200 MiB size limit"):
        DatasetService().prepare(b"x\n1\n123456", "large.csv")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (pd.NA, None),
        (pd.NaT, None),
        (float("nan"), None),
        (np.float64("nan"), None),
        (np.int64(42), 42),
        ("patient-42", "patient-42"),
    ],
)
def test_json_scalar_handles_missing_and_supported_scalar_values(
    value: object,
    expected: str | int | float | bool | None,
) -> None:
    assert DatasetService._json_scalar(value) == expected