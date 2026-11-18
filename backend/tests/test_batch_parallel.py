import csv
import os
from io import StringIO
from pathlib import Path
from threading import Event
from time import monotonic, perf_counter
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.dr.config import PHATEConfig, TSNEConfig, UMAPConfig
from app.models.dataset import PreparedDataset
from app.schemas.embedding import EmbeddingRequest, EmbeddingResult
from app.schemas.execution_plan import ExecutionPlan
from app.services.batches import BatchService, _interleave_runs
from app.services.datasets import DatasetService
from app.storage.local import LocalDatasetStorage


def _synchronized_run(
    run_number: int,
    dataset: PreparedDataset,
    request: EmbeddingRequest,
) -> tuple[int, EmbeddingResult]:
    directory = Path(str(dataset.row_ids[0])).parent
    started_at = perf_counter()
    (directory / f"started-{run_number}").touch()
    deadline = monotonic() + 20
    while len(list(directory.glob("started-*"))) < 2:
        if monotonic() >= deadline:
            raise TimeoutError("Two runs did not execute concurrently.")
        Event().wait(0.01)
    if run_number == 1:
        while not (directory / "completed-2").exists():
            if monotonic() >= deadline:
                raise TimeoutError("Second run did not complete first.")
            Event().wait(0.01)
    (directory / f"completed-{run_number}").touch()
    return run_number, EmbeddingResult(
        embedding_id=uuid4(),
        dataset_id=request.dataset_id,
        algorithm=request.config.algorithm,
        hyperparameters=request.config.model_dump(exclude={"algorithm"}),
        random_seed=request.random_seed,
        observation_count=dataset.metadata.observation_count,
        output_dimensions=request.config.n_components,
        execution_time_seconds=perf_counter() - started_at,
        row_ids=dataset.row_ids,
        coordinates=[[float(os.getpid()), float(run_number)]] * len(dataset.row_ids),
        labels=dataset.labels,
    )


def _failed_run(
    run_number: int,
    dataset: PreparedDataset,
    request: EmbeddingRequest,
) -> tuple[int, EmbeddingResult]:
    raise RuntimeError("Simulated parallel failure")


def _plan(storage: LocalDatasetStorage, directory: Path) -> ExecutionPlan:
    frame = "sample_id,value\n" + "".join(
        f"{(directory / str(index)).as_posix()},{index}\n"
        for index in range(5)
    )
    dataset = DatasetService().prepare(
        frame.encode(), "sample.csv", row_id_column="sample_id"
    )
    storage.save(dataset)
    return ExecutionPlan(
        dataset_id=dataset.metadata.dataset_id,
        observation_count=5,
        n_components=2,
        random_seed=17,
        configs=(TSNEConfig(perplexity=1), TSNEConfig(perplexity=2)),
    )


def test_runs_are_interleaved_without_changing_original_numbers() -> None:
    configs = (
        UMAPConfig(), UMAPConfig(), TSNEConfig(), PHATEConfig(), TSNEConfig()
    )
    runs = list(_interleave_runs(configs))
    assert [number for number, config in runs] == [1, 3, 4, 2, 5]
    assert all(config == configs[number - 1] for number, config in runs)


@pytest.mark.parametrize("workers", [0, -1, True, 1.5])
def test_invalid_worker_count_is_rejected(tmp_path: Path, workers) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        BatchService(LocalDatasetStorage(tmp_path), max_workers=workers)


def test_processes_overlap_and_export_stays_in_plan_order(tmp_path: Path) -> None:
    storage = LocalDatasetStorage(tmp_path / "datasets")
    plan = _plan(storage, tmp_path)
    output = StringIO()
    with patch("app.services.batches._compute_run", _synchronized_run):
        manifest = BatchService(storage, max_workers=2).compute_plan_csv(plan, output)

    rows = list(csv.DictReader(StringIO(output.getvalue())))
    assert [row["tsne_perplexity"] for row in rows] == ["1.0"] * 5 + ["2.0"] * 5
    assert [row["dim2"] for row in rows] == ["1.0"] * 5 + ["2.0"] * 5
    process_ids = {int(float(row["dim1"])) for row in rows}
    assert len(process_ids) == 2
    assert os.getpid() not in process_ids
    assert [record.run_number for record in manifest.runs] == [1, 2]
    assert tuple(record.config for record in manifest.runs) == plan.configs
    assert manifest.random_seed == 17
    assert all(record.execution_time_seconds >= 0 for record in manifest.runs)


def test_parallel_failure_does_not_publish_package(tmp_path: Path) -> None:
    storage = LocalDatasetStorage(tmp_path / "datasets")
    plan = _plan(storage, tmp_path)
    output_directory = tmp_path / "exports"
    with patch("app.services.batches._compute_run", _failed_run):
        with pytest.raises(RuntimeError, match="Simulated parallel failure"):
            BatchService(storage, max_workers=2).compute_plan_package(plan, output_directory)
    assert list(output_directory.iterdir()) == []