from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import numpy as np
import pytest

from app.db import Base, create_database_engine, create_session_factory
from app.models.job import Job, JobStatus
from app.schemas.grid import GridPreviewRequest
from app.services.batches import BatchService
from app.services.datasets import DatasetService
from app.services.job_executor import JobExecutor
from app.services.jobs import JobService
from app.storage.local import LocalDatasetStorage


def test_executor_publishes_package_and_does_not_execute_twice(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(tmp_path / "jobs.sqlite3")
    sessions = create_session_factory(engine)
    storage = LocalDatasetStorage(tmp_path / "datasets")
    dataset = DatasetService().prepare(
        b"value\n1\n2\n3\n4\n5\n", "sample.csv"
    )
    storage.save(dataset)
    request = GridPreviewRequest.model_validate({
        "dataset_id": dataset.metadata.dataset_id,
        "algorithms": {
            "tsne": {
                "perplexity": {"start": "2", "stop": "2", "step": "1"}
            }
        },
    })

    try:
        Base.metadata.create_all(engine)
        jobs = JobService(sessions, storage)
        job_id = jobs.submit(request)
        artifact_root = tmp_path / "artifacts"
        executor = JobExecutor(jobs, BatchService(storage), artifact_root)

        def reduce(*args, **kwargs):
            summary = jobs.get(job_id)
            assert summary is not None
            assert summary.status == JobStatus.RUNNING
            assert summary.started_at is not None
            assert summary.finished_at is None
            assert not list(artifact_root.rglob("*.zip"))
            return np.zeros((5, 2))

        with patch(
            "app.services.embeddings.run_reduction",
            side_effect=reduce,
        ) as reduction:
            assert executor.execute(job_id) is True
            assert executor.execute(job_id) is False
            reduction.assert_called_once()

        summary = jobs.get(job_id)
        assert summary is not None
        assert summary.status == JobStatus.SUCCEEDED
        assert summary.finished_at is not None

        with sessions.begin() as session:
            stored = session.get(Job, job_id)
            assert stored is not None
            assert stored.error_message is None
            assert stored.artifact_path is not None
            artifact_key = stored.artifact_path

        assert artifact_key.startswith("exports/")
        package = artifact_root / artifact_key
        assert package.is_file()
        with ZipFile(package) as archive:
            assert archive.testzip() is None
            assert set(archive.namelist()) == {
                "embeddings.csv", "manifest.json"
            }
    finally:
        engine.dispose()

def test_executor_records_failure_and_cleans_staging_files(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(tmp_path / "failed-job.sqlite3")
    sessions = create_session_factory(engine)
    storage = LocalDatasetStorage(tmp_path / "datasets")
    dataset = DatasetService().prepare(
        b"value\n1\n2\n3\n4\n5\n", "sample.csv"
    )
    storage.save(dataset)
    request = GridPreviewRequest.model_validate({
        "dataset_id": dataset.metadata.dataset_id,
        "algorithms": {
            "tsne": {
                "perplexity": {"start": "2", "stop": "2", "step": "1"}
            }
        },
    })

    try:
        Base.metadata.create_all(engine)
        jobs = JobService(sessions, storage)
        job_id = jobs.submit(request)
        artifact_root = tmp_path / "artifacts"
        executor = JobExecutor(jobs, BatchService(storage), artifact_root)

        with patch(
            "app.services.embeddings.run_reduction",
            side_effect=RuntimeError("Simulated algorithm failure"),
        ) as reduction:
            with pytest.raises(RuntimeError, match="Simulated algorithm failure"):
                executor.execute(job_id)

        reduction.assert_called_once()
        summary = jobs.get(job_id)
        assert summary is not None
        assert summary.status == JobStatus.FAILED
        assert summary.finished_at is not None

        with sessions.begin() as session:
            stored = session.get(Job, job_id)
            assert stored is not None
            assert stored.artifact_path is None
            assert stored.error_message == (
                "Batch execution failed; consult server logs."
            )

        assert not list(artifact_root.rglob("*.zip"))
        assert not list(artifact_root.rglob("*.partial"))
        assert not list(artifact_root.glob(".batch-*"))
    finally:
        engine.dispose()
        