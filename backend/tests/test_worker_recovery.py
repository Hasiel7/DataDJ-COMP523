import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from filelock import FileLock, Timeout

from app import worker_cli
from app.db import Base, create_database_engine, create_session_factory
from app.models.job import JobStatus
from app.schemas.grid import GridPreviewRequest
from app.services.batches import BatchService
from app.services.datasets import DatasetService
from app.services.job_executor import JobExecutor
from app.services.jobs import JobService
from app.storage.local import LocalDatasetStorage


def test_interrupted_job_resumes_under_shared_budget(tmp_path: Path) -> None:
    engine = create_database_engine(tmp_path / "jobs.sqlite3")
    sessions = create_session_factory(engine)
    Base.metadata.create_all(engine)
    storage = LocalDatasetStorage(tmp_path / "datasets")
    dataset = DatasetService().prepare(b"value\n1\n2\n3\n4\n5\n", "sample.csv")
    storage.save(dataset)
    jobs = JobService(sessions, storage)
    request = GridPreviewRequest.model_validate({
        "dataset_id": dataset.metadata.dataset_id,
        "algorithms": {"tsne": {"perplexity": {"start": "1", "stop": "2", "step": "1"}}},
    })
    job_id = jobs.submit(request)
    other_id = jobs.submit(request)
    first = JobExecutor(jobs, BatchService(storage), tmp_path / "artifacts")
    second = JobExecutor(jobs, BatchService(storage), tmp_path / "artifacts")
    try:
        with first.worker_lock.acquire(timeout=0):
            with pytest.raises(Timeout):
                second.execute(other_id)
            assert jobs.get(other_id).status == JobStatus.QUEUED
        with patch(
            "app.services.embeddings.run_reduction",
            side_effect=[np.zeros((5, 2)), KeyboardInterrupt()],
        ):
            with pytest.raises(KeyboardInterrupt):
                first.execute(job_id)
        assert jobs.get(job_id).status == JobStatus.RUNNING
        assert jobs.next_pending() == job_id
        with patch("app.services.embeddings.run_reduction", return_value=np.zeros((5, 2))) as run:
            assert second.execute(job_id)
            run.assert_called_once()
            assert run.call_args.args[2].perplexity == 2
        assert jobs.get(job_id).status == JobStatus.SUCCEEDED
        assert jobs.next_pending() == other_id
        assert not first.execute(job_id)
        with (
            patch("sys.argv", ["worker_cli", "--once", "--max-workers", "1"]),
            patch.object(worker_cli, "create_database_engine", return_value=engine),
            patch.object(worker_cli, "create_session_factory", return_value=sessions),
            patch.object(worker_cli, "LocalDatasetStorage", return_value=storage),
            patch.dict("os.environ", {"DATA_DJ_ARTIFACT_ROOT": str(tmp_path / "artifacts")}),
            patch("app.services.embeddings.run_reduction", return_value=np.zeros((5, 2))) as run,
        ):
            assert worker_cli.main() == 0
            assert run.call_count == 2
        assert jobs.get(other_id).status == JobStatus.SUCCEEDED
        assert jobs.next_pending() is None
    finally:
        engine.dispose()


def test_os_releases_worker_lock_after_process_is_killed(tmp_path: Path) -> None:
    path = tmp_path / "worker.lock"
    program = (
        "import sys; from filelock import FileLock; from threading import Event; "
        "lock = FileLock(sys.argv[1]); lock.acquire(); "
        "print('locked', flush=True); Event().wait()"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", program, str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(Timeout):
            FileLock(path).acquire(timeout=0)
        child.kill()
        child.communicate(timeout=10)
        with FileLock(path).acquire(timeout=0):
            assert path.exists()
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)