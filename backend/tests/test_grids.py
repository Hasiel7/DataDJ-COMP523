from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.routes.datasets import get_dataset_storage
from app.dr.config import Algorithm
from app.main import app
from app.schemas.grid import GridPreviewRequest, NumericRange
from app.services.datasets import DatasetService
from app.services.grids import expand_grid, expand_job_grid
from app.storage.local import LocalDatasetStorage


def test_decimal_range_has_exact_values() -> None:
    grid = NumericRange(start=Decimal("0.1"), stop=Decimal("0.3"), step=Decimal("0.1"))
    assert grid.values() == tuple(map(Decimal, ["0.1", "0.2", "0.3"]))

def test_unreachable_endpoint_is_not_appended() -> None:
    grid = NumericRange(start=Decimal("0"), stop=Decimal("1"), step=Decimal("0.3"))
    assert grid.values() == tuple(map(Decimal, ["0", "0.3", "0.6", "0.9"]))

@pytest.mark.parametrize(
    "parameters",
    [
        {"start": Decimal("0"), "stop": Decimal("5"), "step": Decimal("0")},
        {"start": Decimal("5"), "stop": Decimal("0"), "step": Decimal("1")},
        {"start": Decimal("0"), "stop": Decimal("5"), "step": Decimal("0.000001")},
        {"start": Decimal("NaN"), "stop": Decimal("1"), "step": Decimal("0.1")},
    ],
)
def test_invalid_or_excessive_ranges_are_rejected(parameters) -> None:
    with pytest.raises(ValidationError):
        NumericRange(**parameters)

def test_grid_expands_cartesian_product() -> None:
    configs = expand_grid(
        Algorithm.UMAP,
        {
            "n_neighbors": NumericRange(start=Decimal("5"), stop=Decimal("15"), step=Decimal("5")),
            "min_dist": NumericRange(
                start=Decimal("0.1"), 
                stop=Decimal("0.2"), 
                step=Decimal("0.1")),
        },
        n_samples=30,
    )

    assert len(configs) == 6
    assert all(config.algorithm == "umap" for config in configs)
    assert configs[0].model_dump()["n_neighbors"] == Decimal("5")

def test_grid_rejects_excessive_run_count() -> None:
    with pytest.raises(ValueError, match="limit is 2"):
        expand_grid(
            Algorithm.UMAP,
            {"n_neighbors": NumericRange(start=Decimal("5"), 
                                         stop=Decimal("15"), 
                                         step=Decimal("5"))},
            n_samples=30,
            max_runs=2,
        )

def test_grid_rejects_fractional_integer_parameter() -> None:
    with pytest.raises(ValidationError):
        expand_grid(
            Algorithm.PHATE,
            {"knn": NumericRange(start=Decimal("2.5"), stop=Decimal("2.5"), step=Decimal("1"))},
            n_samples=30,
        )


def test_grid_rejects_sample_dependent_parameter() -> None:
    with pytest.raises(ValueError, match="perplexity"):
        expand_grid(Algorithm.TSNE, {}, n_samples=20)

def test_job_combines_algorithms_without_cross_product() -> None:
    request = GridPreviewRequest(
        dataset_id=uuid4(),
        algorithms={
            Algorithm.UMAP: {},
            Algorithm.TSNE: {},
            Algorithm.PHATE: {},
        },
    )
    configs = expand_job_grid(request, n_samples=100)

    assert len(configs) == 3
    assert [config.algorithm for config in configs] == [
        "umap", "tsne", "phate"
    ]


def test_job_rejects_combined_count_over_limit() -> None:
    request = GridPreviewRequest(
        dataset_id=uuid4(),
        algorithms={
            Algorithm.UMAP: {
                "n_neighbors": NumericRange(
                    start=Decimal("2"), 
                    stop=Decimal("14"), 
                    step=Decimal("1")),
            },
            Algorithm.PHATE: {
                "knn": NumericRange(start=Decimal("2"), stop=Decimal("14"), step=Decimal("1"))
            },
        },
    )

    with pytest.raises(ValueError, match="Total grid contains 26 runs"):
        expand_job_grid(request, n_samples=100)

def test_preview_endpoint_returns_validated_configurations(tmp_path: Path) -> None:
    storage = LocalDatasetStorage(tmp_path)
    dataset = DatasetService().prepare(
        b"value\n1\n2\n3\n4\n5\n",
        "sample.csv",
    )
    storage.save(dataset)
    app.dependency_overrides[get_dataset_storage] = lambda: storage

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/grids/preview",
                json={
                    "dataset_id": str(dataset.metadata.dataset_id),
                    "algorithms": {
                        "tsne": {
                            "perplexity": {
                                "start": "1",
                                "stop": "2",
                                "step": "1",
                            }
                        }
                    },
                },
            )
    finally:
        app.dependency_overrides.pop(get_dataset_storage, None)

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["observation_count"] == 5
    assert result["total_runs"] == 2
    assert result["runs_by_algorithm"] == {"tsne": 2}
    assert [
        config["perplexity"] for config in result["configs"]
    ] == [1.0, 2.0]

@pytest.mark.parametrize(
    ("algorithms", "missing_dataset", "expected_status", "message"),
    [
        ({"tsne": {}}, True, 404, "Dataset not found"),
        (
            {"tsne": {"perplexity": {"start": "5", "stop": "5", "step": "1"}}},
            False, 422, "perplexity",
        ),
        (
            {"umap": {"unknown": {"start": "1", "stop": "1", "step": "1"}}},
            False, 422, "Unknown parameters",
        ),
        (
            {"umap": {"n_epochs": {"start": "1", "stop": "26", "step": "1"}}},
            False, 422, "26 runs",
        ),
        (
            {"tsne": {"perplexity": {"start": "1", "stop": "2", "step": "0"}}},
            False, 422, "step",
        ),
    ],
)
def test_preview_endpoint_rejects_invalid_requests(
    tmp_path: Path,
    algorithms: dict[str, dict[str, dict[str, str]]],
    missing_dataset: bool,
    expected_status: int,
    message: str,
) -> None:
    storage = LocalDatasetStorage(tmp_path)
    dataset = DatasetService().prepare(
        b"value\n1\n2\n3\n4\n5\n", "sample.csv"
    )
    storage.save(dataset)
    dataset_id = uuid4() if missing_dataset else dataset.metadata.dataset_id
    app.dependency_overrides[get_dataset_storage] = lambda: storage

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/grids/preview",
                json={
                    "dataset_id": str(dataset_id),
                    "algorithms": algorithms,
                },
            )
    finally:
        app.dependency_overrides.pop(get_dataset_storage, None)

    assert response.status_code == expected_status, response.text
    assert message in response.text