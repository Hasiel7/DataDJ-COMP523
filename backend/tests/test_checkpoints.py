from io import StringIO
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from app.dr.config import TSNEConfig
from app.schemas.execution_plan import ExecutionPlan
from app.services.batches import BatchService
from app.services.datasets import DatasetService
from app.storage.local import LocalDatasetStorage


@pytest.mark.parametrize("corrupt", ["broken", '[]', '{"result": 4, "sha256": "x"}'])
def test_restart_reuses_completed_runs_and_ignores_corrupt_checkpoint(
    tmp_path: Path, corrupt: str
) -> None:
    storage = LocalDatasetStorage(tmp_path / "datasets")
    dataset = DatasetService().prepare(b"value\n1\n2\n3\n4\n5\n", "sample.csv")
    storage.save(dataset)
    plan = ExecutionPlan(
        dataset_id=dataset.metadata.dataset_id, observation_count=5,
        n_components=2, random_seed=42,
        configs=(TSNEConfig(perplexity=1), TSNEConfig(perplexity=2)),
    )
    checkpoints = tmp_path / "checkpoints"
    with patch(
        "app.services.embeddings.run_reduction",
        side_effect=[np.zeros((5, 2)), KeyboardInterrupt()],
    ):
        with pytest.raises(KeyboardInterrupt):
            BatchService(storage).compute_csv_with_manifest(
                plan, StringIO(), checkpoint_directory=checkpoints
            )
    assert len(list(checkpoints.rglob("*.json"))) == 1
    with patch("app.services.embeddings.run_reduction", return_value=np.zeros((5, 2))) as run:
        manifest = BatchService(storage).compute_csv_with_manifest(
            plan, StringIO(), checkpoint_directory=checkpoints
        )
        run.assert_called_once()
        assert run.call_args.args[2].perplexity == 2
    first_id = manifest.runs[0].embedding_id
    with patch("app.services.embeddings.run_reduction") as run:
        restored = BatchService(storage, max_workers=2).compute_csv_with_manifest(
            plan, StringIO(), checkpoint_directory=checkpoints
        )
        run.assert_not_called()
    assert restored.runs[0].embedding_id == first_id
    next(checkpoints.rglob("1.json")).write_text(corrupt, encoding="utf-8")
    with patch("app.services.embeddings.run_reduction", return_value=np.zeros((5, 2))) as run:
        BatchService(storage).compute_csv_with_manifest(
            plan, StringIO(), checkpoint_directory=checkpoints
        )
        run.assert_called_once()
        assert run.call_args.args[2].perplexity == 1


def test_changed_seed_does_not_reuse_results(tmp_path: Path) -> None:
    storage = LocalDatasetStorage(tmp_path / "datasets")
    dataset = DatasetService().prepare(b"value\n1\n2\n3\n4\n5\n", "sample.csv")
    storage.save(dataset)
    plan = ExecutionPlan(
        dataset_id=dataset.metadata.dataset_id, observation_count=5,
        n_components=2, random_seed=42,
        configs=(TSNEConfig(perplexity=1),),
    )
    with patch("app.services.embeddings.run_reduction", return_value=np.zeros((5, 2))) as run:
        for seed in (42, 17):
            BatchService(storage).compute_csv_with_manifest(
                plan.model_copy(update={"random_seed": seed}), StringIO(),
                checkpoint_directory=tmp_path / "checkpoints",
            )
        assert run.call_count == 2