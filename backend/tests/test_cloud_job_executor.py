from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from azure.storage.blob import ContainerClient

from app.schemas.execution_plan import ExecutionPlan
from app.services.batches import BatchService
from app.services.cloud_job_executor import CloudJobExecutor
from app.services.jobs import JobService, LeaseLostError
from app.storage.published_checkpoints import PublishedBlobCheckpoints


@pytest.fixture
def setup_executor():
    jobs = Mock(spec=JobService)
    batches = Mock(spec=BatchService)
    container = Mock(spec=ContainerClient)

    job_id = uuid4()
    token = uuid4()
    plan = Mock(spec=ExecutionPlan)

    jobs.claim_with_lease.return_value = (token, plan)
    jobs.succeed_with_lease.return_value = True
    jobs.fail_with_lease.return_value = True

    with (
        patch("app.services.cloud_job_executor.LeaseHeartbeat") as heartbeat_class,
        patch("app.services.cloud_job_executor.BlobJobExports") as exports_class,
    ):
        heartbeat = heartbeat_class.return_value
        exports = exports_class.return_value
        exports.upload.return_value = "jobs/test/attempts/test/export.zip"

        executor = CloudJobExecutor(jobs, batches, container)

        yield executor, jobs, batches, heartbeat, exports, job_id, token, plan


def test_success_publishes_and_cleans_temporary_package(setup_executor):
    executor, jobs, batches, heartbeat, exports, job_id, token, plan = setup_executor
    created_paths = []

    def compute(received_plan, directory, *, checkpoint_factory):
        assert received_plan is plan
        assert isinstance(checkpoint_factory("identity"), PublishedBlobCheckpoints)

        package = directory / "batch.zip"
        package.write_bytes(b"test package")
        created_paths.append(package)
        return package

    batches.compute_plan_package.side_effect = compute

    assert executor.execute(job_id) is True

    heartbeat.start.assert_called_once()
    heartbeat.stop.assert_called_once()
    exports.upload.assert_called_once_with(job_id, token, created_paths[0])
    jobs.succeed_with_lease.assert_called_once_with(
        job_id, token, "jobs/test/attempts/test/export.zip"
    )
    jobs.fail_with_lease.assert_not_called()
    assert not created_paths[0].exists()


def test_unclaimed_job_does_not_start_work(setup_executor):
    executor, jobs, batches, heartbeat, exports, job_id, _, _ = setup_executor
    jobs.claim_with_lease.return_value = None

    assert executor.execute(job_id) is False

    heartbeat.start.assert_not_called()
    batches.compute_plan_package.assert_not_called()
    exports.upload.assert_not_called()


@pytest.mark.parametrize("stage", ["compute", "upload"])
def test_execution_failure_is_recorded(setup_executor, stage):
    executor, jobs, batches, heartbeat, exports, job_id, token, _ = setup_executor

    def compute(_plan, directory, *, checkpoint_factory):
        package = directory / "batch.zip"
        package.write_bytes(b"test package")
        return package

    batches.compute_plan_package.side_effect = compute
    if stage == "compute":
        batches.compute_plan_package.side_effect = RuntimeError("Compute failed")
    else:
        exports.upload.side_effect = RuntimeError("Upload failed")

    with pytest.raises(RuntimeError, match="failed"):
        executor.execute(job_id)

    heartbeat.stop.assert_called_once()
    jobs.fail_with_lease.assert_called_once_with(
        job_id, token, "Batch execution failed; consult worker logs."
    )
    jobs.succeed_with_lease.assert_not_called()


def test_lost_lease_does_not_record_failure(setup_executor):
    executor, jobs, batches, heartbeat, _, job_id, _, _ = setup_executor
    batches.compute_plan_package.side_effect = LeaseLostError("Lease lost")

    with pytest.raises(LeaseLostError):
        executor.execute(job_id)

    heartbeat.stop.assert_called_once()
    jobs.fail_with_lease.assert_not_called()
    jobs.succeed_with_lease.assert_not_called()


def test_heartbeat_shutdown_failure_prevents_completion(setup_executor):
    executor, jobs, batches, heartbeat, _, job_id, _, _ = setup_executor

    def compute(_plan, directory, *, checkpoint_factory):
        package = directory / "batch.zip"
        package.write_bytes(b"test package")
        return package

    batches.compute_plan_package.side_effect = compute
    heartbeat.stop.side_effect = TimeoutError("Heartbeat did not stop")
    heartbeat.raise_if_lost.side_effect = [
        None,
        None,
        None,
        LeaseLostError("Ownership is uncertain"),
    ]

    with pytest.raises(LeaseLostError, match="uncertain"):
        executor.execute(job_id)

    jobs.fail_with_lease.assert_not_called()
    jobs.succeed_with_lease.assert_not_called()


def test_rejected_success_raises_lease_lost(setup_executor):
    executor, jobs, batches, _, _, job_id, _, _ = setup_executor

    def compute(_plan, directory, *, checkpoint_factory):
        package = directory / "batch.zip"
        package.write_bytes(b"test package")
        return package

    batches.compute_plan_package.side_effect = compute
    jobs.succeed_with_lease.return_value = False

    with pytest.raises(LeaseLostError, match="before success"):
        executor.execute(job_id)

    jobs.fail_with_lease.assert_not_called()