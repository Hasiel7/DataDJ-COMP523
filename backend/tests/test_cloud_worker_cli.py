from unittest.mock import MagicMock, Mock, call, patch
from uuid import uuid4

import pytest

from app import cloud_worker_cli
from app.services.jobs import LeaseLostError


@pytest.mark.parametrize("lost", [False, True])
def test_cloud_worker_drains_leasable_jobs(monkeypatch, lost):
    monkeypatch.setenv("DATA_DJ_DATABASE_URL", "postgresql+psycopg://localhost/test")
    monkeypatch.setenv("DATA_DJ_BLOB_ACCOUNT_URL", "https://example.blob.core.windows.net")
    monkeypatch.setattr("sys.argv", ["cloud_worker_cli", "--once"])
    job_id = uuid4()
    engine = MagicMock()
    jobs = MagicMock()
    jobs.next_leasable.side_effect = [job_id, None]
    executor = MagicMock()
    if lost:
        executor.execute.side_effect = LeaseLostError("lost")

    with (
        patch.object(cloud_worker_cli.signal, "signal"),
        patch.object(cloud_worker_cli, "create_database_engine", return_value=engine),
        patch.object(cloud_worker_cli, "ManagedIdentityCredential"),
        patch.object(cloud_worker_cli, "BlobServiceClient"),
        patch.object(cloud_worker_cli, "JobService", return_value=jobs),
        patch.object(cloud_worker_cli, "CloudJobExecutor", return_value=executor),
    ):
        assert cloud_worker_cli.main() == (1 if lost else 0)
    executor.execute.assert_called_once_with(job_id)
    assert all(call.kwargs == {"owned_only": True} for call in jobs.next_leasable.call_args_list)
    engine.dispose.assert_called_once()


def test_cloud_worker_drains_imports_and_jobs_fairly(monkeypatch):
    monkeypatch.setenv("DATA_DJ_DATABASE_URL", "postgresql+psycopg://localhost/test")
    monkeypatch.setenv("DATA_DJ_BLOB_ACCOUNT_URL", "https://example.blob.core.windows.net")
    monkeypatch.setenv("DATA_DJ_DIRECT_BATCH_UPLOADS", "1")
    monkeypatch.setattr("sys.argv", ["cloud_worker_cli", "--once"])
    job_id, import_id, next_import_id = uuid4(), uuid4(), uuid4()
    jobs = Mock()
    jobs.next_leasable.side_effect = [job_id, job_id, None, None]
    imports = Mock()
    imports.next_leasable.side_effect = [import_id, next_import_id, next_import_id, None]
    imports.execute.return_value = True
    executor = Mock()
    calls = Mock()
    calls.attach_mock(imports.execute, "import_execute")
    calls.attach_mock(executor.execute, "job_execute")
    with (
        patch.object(cloud_worker_cli.signal, "signal"),
        patch.object(cloud_worker_cli, "create_database_engine"),
        patch.object(cloud_worker_cli, "ManagedIdentityCredential"),
        patch.object(cloud_worker_cli, "BlobServiceClient"),
        patch.object(cloud_worker_cli, "JobService", return_value=jobs),
        patch.object(cloud_worker_cli, "CloudJobExecutor", return_value=executor),
        patch.object(cloud_worker_cli, "BatchImports", return_value=imports),
    ):
        assert cloud_worker_cli.main() == 0
    assert calls.mock_calls == [
        call.import_execute(import_id),
        call.job_execute(job_id),
        call.import_execute(next_import_id),
    ]
