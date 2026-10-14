import csv
from collections.abc import Iterator
from io import StringIO
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.routes.datasets import get_dataset_storage
from app.api.routes.embeddings import get_embedding_storage
from app.main import app
from app.services.embeddings import MAX_EMBEDDING_OBSERVATIONS
from app.storage.embeddings import InMemoryEmbeddingStorage
from app.storage.local import LocalDatasetStorage


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app.dependency_overrides[get_dataset_storage] = lambda: LocalDatasetStorage(tmp_path)
    app.dependency_overrides[get_embedding_storage] = lambda: app.state.test_embedding_storage
    app.state.test_embedding_storage = InMemoryEmbeddingStorage()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_dataset_storage, None)
    app.dependency_overrides.pop(get_embedding_storage, None)
    del app.state.test_embedding_storage


def upload_dataset(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/datasets",
        files={
            "file": (
                "samples.csv",
                (
                    b"sample_id,label,x,y\n"
                    b"S1,case,0.0,0.1\n"
                    b"S2,control,0.1,0.0\n"
                    b"S3,case,0.2,0.3\n"
                    b"S4,control,0.4,0.2\n"
                    b"S5,case,0.5,0.7\n"
                    b"S6,control,0.8,0.6\n"
                    b"S7,case,1.0,0.9\n"
                    b"S8,control,1.2,1.1\n"
                ),
                "text/csv",
            )
        },
        data={
            "label_column": "label",
            "row_id_column": "sample_id",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_compute_embedding_returns_csv_and_can_be_retrieved_as_json(
    client: TestClient,
) -> None:
    dataset = upload_dataset(client)
    response = client.post(
        "/api/embeddings",
        json={
            "dataset_id": dataset["dataset_id"],
            "config": {
                "algorithm": "tsne",
                "perplexity": 2,
                "learning_rate": 100,
                "max_iter": 250,
            },
            "random_seed": 17,
        },
    )

    assert response.status_code == 201
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"].endswith('.csv"')
    rows = list(csv.DictReader(StringIO(response.text)))
    assert list(rows[0]) == ["row_id", "label", "embedding_1", "embedding_2"]
    assert [row["row_id"] for row in rows] == [f"S{index}" for index in range(1, 9)]
    assert [row["label"] for row in rows] == ["case", "control"] * 4
    assert all(row["embedding_1"] and row["embedding_2"] for row in rows)

    embedding_id = response.headers["x-embedding-id"]
    assert UUID(embedding_id)
    fetched = client.get(f"/api/embeddings/{embedding_id}")
    assert fetched.status_code == 200
    result = fetched.json()
    assert result["embedding_id"] == embedding_id
    assert result["dataset_id"] == dataset["dataset_id"]
    assert result["algorithm"] == "tsne"
    assert result["hyperparameters"] == {
        "perplexity": 2.0,
        "learning_rate": 100.0,
        "max_iter": 250,
        "n_components": 2,
    }
    assert result["random_seed"] == 17
    assert result["observation_count"] == 8
    assert result["output_dimensions"] == 2
    assert result["execution_time_seconds"] >= 0
    assert result["row_ids"] == [f"S{index}" for index in range(1, 9)]
    assert result["labels"] == ["case", "control"] * 4
    assert len(result["coordinates"]) == 8
    assert all(len(point) == 2 for point in result["coordinates"])


def test_missing_dataset_returns_404(client: TestClient) -> None:
    response = client.post(
        "/api/embeddings",
        json={
            "dataset_id": "00000000-0000-0000-0000-000000000000",
            "config": {"algorithm": "umap", "n_neighbors": 2},
        },
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Dataset not found."}


def test_three_dimensional_embedding_csv_has_three_coordinate_columns(
    client: TestClient,
) -> None:
    dataset = upload_dataset(client)
    response = client.post(
        "/api/embeddings",
        json={
            "dataset_id": dataset["dataset_id"],
            "config": {
                "algorithm": "tsne",
                "n_components": 3,
                "perplexity": 2,
                "max_iter": 250,
            },
        },
    )

    assert response.status_code == 201, response.text
    rows = list(csv.DictReader(StringIO(response.text)))
    assert list(rows[0]) == ["row_id", "label", "embedding_1", "embedding_2", "embedding_3"]
    assert len(rows) == 8
    assert all(row["embedding_3"] for row in rows)

    embedding_id = response.headers["x-embedding-id"]
    result = client.get(f"/api/embeddings/{embedding_id}").json()
    assert result["output_dimensions"] == 3
    assert all(len(point) == 3 for point in result["coordinates"])


def test_sample_dependent_parameter_error_returns_422(client: TestClient) -> None:
    dataset = upload_dataset(client)
    response = client.post(
        "/api/embeddings",
        json={
            "dataset_id": dataset["dataset_id"],
            "config": {"algorithm": "tsne", "perplexity": 8},
        },
    )

    assert response.status_code == 422
    assert "perplexity must be less" in response.json()["detail"]


def test_embedding_observation_limit_is_100000() -> None:
    assert MAX_EMBEDDING_OBSERVATIONS == 100_000


def test_embedding_request_above_configured_observation_limit_returns_413(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = upload_dataset(client)
    monkeypatch.setattr("app.services.embeddings.MAX_EMBEDDING_OBSERVATIONS", 7)

    response = client.post(
        "/api/embeddings",
        json={
            "dataset_id": dataset["dataset_id"],
            "config": {"algorithm": "tsne", "perplexity": 2},
        },
    )

    assert response.status_code == 413
    assert "7 observations" in response.json()["detail"]


def test_invalid_config_and_seed_are_rejected_by_request_schema(client: TestClient) -> None:
    dataset = upload_dataset(client)
    invalid_config = client.post(
        "/api/embeddings",
        json={
            "dataset_id": dataset["dataset_id"],
            "config": {"algorithm": "umap", "n_neighbors": 1},
        },
    )
    invalid_seed = client.post(
        "/api/embeddings",
        json={
            "dataset_id": dataset["dataset_id"],
            "config": {"algorithm": "phate", "knn": 2},
            "random_seed": -1,
        },
    )

    assert invalid_config.status_code == 422
    assert invalid_seed.status_code == 422


def test_unknown_embedding_returns_404(client: TestClient) -> None:
    response = client.get("/api/embeddings/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json() == {"detail": "Embedding not found."}