import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import numpy as np

from app.dr.config import TSNEConfig
from app.schemas.batch import BatchManifest, BatchRunRecord
from app.schemas.grid import GridPreviewRequest
from app.services.batches import BatchService
from app.services.datasets import DatasetService
from app.storage.local import LocalDatasetStorage


def test_run_record_serializes_algorithm_config() -> None:
    record = BatchRunRecord(
        run_number=1,
        embedding_id=uuid4(),
        config=TSNEConfig(perplexity=2, max_iter=250),
        observation_count=5,
        execution_time_seconds=0.25,
    )

    payload = json.loads(record.model_dump_json())

    assert payload["config"]["algorithm"] == "tsne"
    assert payload["config"]["perplexity"] == 2.0
    assert payload["config"]["max_iter"] == 250
    assert payload["run_number"] == 1
    assert BatchRunRecord.model_validate_json(record.model_dump_json()) == record


def test_batch_manifest_records_all_configurations(tmp_path: Path) -> None:
    storage = LocalDatasetStorage(tmp_path)
    dataset = DatasetService().prepare(
        b"value\n1\n2\n3\n4\n5\n", "sample.csv"
    )
    storage.save(dataset)
    request = GridPreviewRequest.model_validate(
        {
            "dataset_id": dataset.metadata.dataset_id,
            "algorithms": {
                "tsne": {
                    "perplexity": {"start": "1", "stop": "2", "step": "1"}
                }
            }
        }
    )

    with patch(
        "app.services.embeddings.run_reduction",
        return_value=np.zeros((5, 2)),
    ):
        manifest = BatchService(storage).compute_csv_with_manifest(
            request, StringIO(newline="")
        )

    assert [run.run_number for run in manifest.runs] == [1, 2]
    assert all(run.observation_count == 5 for run in manifest.runs)
    assert manifest.dataset.dataset_id == dataset.metadata.dataset_id
    assert manifest.csv_filename == "embeddings.csv"
    assert "umap-learn" in manifest.package_versions
    serialized = manifest.model_dump_json()
    round_tripped = BatchManifest.model_validate_json(serialized)
    assert round_tripped == manifest