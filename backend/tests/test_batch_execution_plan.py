from io import StringIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import numpy as np
import pytest

from app.dr.config import TSNEConfig
from app.schemas.batch import BatchManifest
from app.schemas.execution_plan import ExecutionPlan
from app.services.batches import BatchService
from app.services.datasets import DatasetService
from app.storage.local import LocalDatasetStorage


def test_batch_executes_saved_plan_without_expanding_ranges(tmp_path: Path) -> None:
    storage = LocalDatasetStorage(tmp_path)
    dataset = DatasetService().prepare(
        b"value\n1\n2\n3\n4\n5\n", "sample.csv"
    )
    storage.save(dataset)
    plan = ExecutionPlan(
        dataset_id=dataset.metadata.dataset_id,
        observation_count=5,
        n_components=2,
        random_seed=17,
        configs=(TSNEConfig(perplexity=2, max_iter=350),),
    )

    with (
        patch(
            "app.services.batches.expand_job_grid",
            side_effect=AssertionError("Saved plans must not be re-expanded"),
        ) as expand,
        patch(
            "app.services.embeddings.run_reduction",
            return_value=np.zeros((5, 2)),
        ) as reduce,
    ):
        manifest = BatchService(storage).compute_plan_csv(plan, StringIO())

    expand.assert_not_called()
    reduce.assert_called_once()
    assert reduce.call_args.args[2] == plan.configs[0]
    assert reduce.call_args.args[3] == 17
    assert manifest.runs[0].config == plan.configs[0]

def test_saved_plan_rejects_changed_observation_count(tmp_path: Path) -> None:
    storage = LocalDatasetStorage(tmp_path / "datasets")
    dataset = DatasetService().prepare(
        b"value\n1\n2\n3\n4\n5\n", "sample.csv"
    )
    storage.save(dataset)
    plan = ExecutionPlan(
        dataset_id=dataset.metadata.dataset_id,
        observation_count=6,
        n_components=2,
        random_seed=17,
        configs=(TSNEConfig(perplexity=2),),
    )
    output = StringIO()

    with patch("app.services.embeddings.run_reduction") as reduce:
        with pytest.raises(ValueError, match="observation count differs"):
            BatchService(storage).compute_plan_csv(plan, output)

    reduce.assert_not_called()
    assert output.getvalue() == ""

def test_saved_plan_package_preserves_configs_and_seed(tmp_path: Path) -> None:
    storage = LocalDatasetStorage(tmp_path / "datasets")
    dataset = DatasetService().prepare(
        b"value\n1\n2\n3\n4\n5\n", "sample.csv"
    )
    storage.save(dataset)
    plan = ExecutionPlan(
        dataset_id=dataset.metadata.dataset_id,
        observation_count=5,
        n_components=2,
        random_seed=17,
        configs=(
            TSNEConfig(perplexity=1, max_iter=350),
            TSNEConfig(perplexity=2, max_iter=450),
        ),
    )

    with (
        patch(
            "app.services.batches.expand_job_grid",
            side_effect=AssertionError("Saved plans must not be re-expanded"),
        ) as expand,
        patch(
            "app.services.embeddings.run_reduction",
            return_value=np.zeros((5, 2)),
        ) as reduce,
    ):
        package = BatchService(storage).compute_plan_package(
            plan, tmp_path / "exports"
        )

    expand.assert_not_called()
    assert reduce.call_count == 2
    assert [call.args[2] for call in reduce.call_args_list] == list(plan.configs)
    assert all(call.args[3] == 17 for call in reduce.call_args_list)

    with ZipFile(package) as archive:
        assert set(archive.namelist()) == {"embeddings.csv", "manifest.json"}
        assert archive.testzip() is None
        manifest = BatchManifest.model_validate_json(
            archive.read("manifest.json")
        )

    assert manifest.random_seed == 17
    assert tuple(run.config for run in manifest.runs) == plan.configs