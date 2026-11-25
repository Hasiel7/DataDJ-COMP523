import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from threading import Barrier, Event
from unittest.mock import Mock, patch
from uuid import uuid4
from zipfile import ZipFile

import numpy as np
import pytest
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import ContainerClient
from sqlalchemy import URL, Interval, create_engine, delete, func, literal, select, text, update
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from app.db import create_database_engine, create_session_factory
from app.dr.config import TSNEConfig
from app.models.job import Job, JobCheckpoint, JobStatus
from app.schemas.batch import BatchManifest
from app.schemas.embedding import EmbeddingRequest
from app.schemas.execution_plan import ExecutionPlan
from app.services.batches import BatchService
from app.services.cloud_job_executor import CloudJobExecutor
from app.services.datasets import DatasetService
from app.services.jobs import JobService, LeaseLostError
from app.services.lease_heartbeat import LeaseHeartbeat
from app.storage.checkpoints import checkpoint_blob_key, checkpoint_identity_hash
from app.storage.completed_blob_results import CompletedBlobResults
from app.storage.local import LocalDatasetStorage
from app.storage.published_checkpoints import PublishedBlobCheckpoints


def test_concurrent_claim_and_expired_lease_recovery(tmp_path: Path) -> None:
    url = URL.create(
        "postgresql+psycopg",
        username="datadj_test",
        password=os.environ["DATADJ_TEST_POSTGRES_PASSWORD"],
        host="127.0.0.1",
        port=55432,
        database="datadj_lease_test",
    )
    engine = create_engine(url)
    sessions = create_session_factory(engine)
    service = JobService(sessions, LocalDatasetStorage(tmp_path / "datasets"))

    job_id = uuid4()
    dataset_id = uuid4()
    plan = ExecutionPlan.model_validate({
        "dataset_id": str(dataset_id),
        "observation_count": 5,
        "n_components": 2,
        "random_seed": 42,
        "configs": [{"algorithm": "tsne", "perplexity": 2}],
    })

    def compete():
        barrier = Barrier(2)

        def attempt():
            barrier.wait(timeout=15)
            return service.claim_with_lease(job_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt) for _ in range(2)]
            return [future.result() for future in futures]

    try:
        with sessions.begin() as session:
            session.add(
                Job(
                    id=job_id,
                    dataset_id=dataset_id,
                    request_payload={},
                    execution_plan=plan.model_dump(mode="json"),
                )
            )

        first_results = compete()
        first_winners = [result for result in first_results if result is not None]
        assert len(first_winners) == 1
        assert first_winners[0][1] == plan

        first_token = first_winners[0][0]
        assert not service.renew_lease(job_id, uuid4())

        # Shorten the lease, then verify renewal extends it.
        with sessions.begin() as session:
            session.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    lease_expires_at=func.clock_timestamp()
                    + literal(timedelta(minutes=1), type_=Interval())
                )
            )
            before = session.scalar(
                select(Job.lease_expires_at).where(Job.id == job_id)
            )

        assert service.renew_lease(job_id, first_token)

        with sessions.begin() as session:
            after = session.scalar(
                select(Job.lease_expires_at).where(Job.id == job_id)
            )
        assert before is not None
        assert after is not None
        assert after > before

        # Expire the lease in the test database without waiting five minutes.
        with sessions.begin() as session:
            session.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    lease_expires_at=func.clock_timestamp()
                    - literal(timedelta(seconds=1), type_=Interval())
                )
            )
        assert not service.renew_lease(job_id, first_token)

        second_results = compete()
        second_winners = [result for result in second_results if result is not None]
        assert len(second_winners) == 1
        assert second_winners[0][0] != first_winners[0][0]
        assert second_winners[0][1] == plan

        second_token = second_winners[0][0]

        assert not service.renew_lease(job_id, first_token)
        assert service.renew_lease(job_id, second_token)

        with sessions.begin() as session:
            session.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(status=JobStatus.SUCCEEDED)
            )

        assert not service.renew_lease(job_id, second_token)
        
    finally:
        with sessions.begin() as session:
            session.execute(delete(Job).where(Job.id == job_id))
        engine.dispose()

@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_leased_completion_rejects_stale_workers(
    tmp_path: Path, outcome: str
) -> None:
    url = URL.create(
        "postgresql+psycopg",
        username="datadj_test",
        password=os.environ["DATADJ_TEST_POSTGRES_PASSWORD"],
        host="127.0.0.1",
        port=55432,
        database="datadj_lease_test",
    )
    engine = create_engine(url)
    sessions = create_session_factory(engine)
    service = JobService(sessions, LocalDatasetStorage(tmp_path / "datasets"))

    job_id = uuid4()
    dataset_id = uuid4()
    plan = ExecutionPlan.model_validate({
        "dataset_id": str(dataset_id),
        "observation_count": 5,
        "n_components": 2,
        "random_seed": 42,
        "configs": [{"algorithm": "tsne", "perplexity": 2}],
    })

    def finish(token):
        if outcome == "success":
            return service.succeed_with_lease(job_id, token, "exports/result.zip")
        return service.fail_with_lease(job_id, token, "Computation failed.")

    try:
        with sessions.begin() as session:
            session.add(
                Job(
                    id=job_id,
                    dataset_id=dataset_id,
                    request_payload={},
                    execution_plan=plan.model_dump(mode="json"),
                )
            )

        first_claim = service.claim_with_lease(job_id)
        assert first_claim is not None
        first_token, _ = first_claim

        assert not finish(uuid4())

        # The older, token-free methods must not bypass an active lease.
        assert service.claim(job_id, resume=True) is None
        assert not service.succeed(job_id, "exports/result.zip")
        assert not service.fail(job_id, "Failure.")

        with pytest.raises(ValueError, match="relative ZIP path"):
            service.succeed_with_lease(job_id, first_token, "../escape.zip")
        with pytest.raises(ValueError, match="2000 characters"):
            service.fail_with_lease(job_id, first_token, " ")

        with sessions.begin() as session:
            session.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    lease_expires_at=func.clock_timestamp()
                    - literal(timedelta(seconds=1), type_=Interval())
                )
            )

        assert not finish(first_token)
        assert service.claim(job_id, resume=True) is None

        second_claim = service.claim_with_lease(job_id)
        assert second_claim is not None
        second_token, _ = second_claim
        assert second_token != first_token

        assert not finish(first_token)
        assert finish(second_token)
        assert not finish(second_token)

        with sessions.begin() as session:
            job = session.get(Job, job_id)
            assert job is not None
            assert job.status == (
                JobStatus.SUCCEEDED if outcome == "success" else JobStatus.FAILED
            )
            assert job.finished_at is not None
            assert job.claim_token is None
            assert job.lease_expires_at is None
            assert job.artifact_path == (
                "exports/result.zip" if outcome == "success" else None
            )
            assert job.error_message == (
                None if outcome == "success" else "Computation failed."
            )
    finally:
        with sessions.begin() as session:
            session.execute(delete(Job).where(Job.id == job_id))
        engine.dispose()

def test_next_leasable_skips_ineligible_jobs(tmp_path: Path) -> None:
    url = URL.create(
        "postgresql+psycopg",
        username="datadj_test",
        password=os.environ["DATADJ_TEST_POSTGRES_PASSWORD"],
        host="127.0.0.1",
        port=55432,
        database="datadj_lease_test",
    )
    engine = create_engine(url)
    sessions = create_session_factory(engine)
    service = JobService(sessions, LocalDatasetStorage(tmp_path / "datasets"))

    ids = {
        name: uuid4()
        for name in ("queued", "active", "expired", "legacy", "unplanned", "done")
    }
    dataset_id = uuid4()
    plan = ExecutionPlan.model_validate({
        "dataset_id": str(dataset_id),
        "observation_count": 5,
        "n_components": 2,
        "random_seed": 42,
        "configs": [{"algorithm": "tsne", "perplexity": 2}],
    }).model_dump(mode="json")

    try:
        with sessions.begin() as session:
            # This test uses the dedicated, disposable database.
            assert session.scalar(select(func.count()).select_from(Job)) == 0

            for name, job_id in ids.items():
                if name == "done":
                    status = JobStatus.SUCCEEDED
                elif name in ("queued", "unplanned"):
                    status = JobStatus.QUEUED
                else:
                    status = JobStatus.RUNNING

                lease_expires_at = None
                if name == "active":
                    lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
                elif name == "expired":
                    lease_expires_at = datetime.now(UTC) - timedelta(minutes=1)

                session.add(
                    Job(
                        id=job_id,
                        dataset_id=dataset_id,
                        request_payload={},
                        execution_plan=None if name == "unplanned" else plan,
                        status=status,
                        created_at=datetime(2020, 1, 1, tzinfo=UTC),
                        claim_token=uuid4() if name in ("active", "expired") else None,
                        lease_expires_at=lease_expires_at,
                    )
                )

        assert service.next_leasable() == ids["expired"]
        assert service.claim_with_lease(ids["expired"]) is not None

        assert service.next_leasable() == ids["queued"]
        assert service.claim_with_lease(ids["queued"]) is not None

        assert service.next_leasable() is None
    finally:
        with sessions.begin() as session:
            session.execute(delete(Job).where(Job.id.in_(ids.values())))
        engine.dispose()

@pytest.fixture
def checkpoint_job(tmp_path: Path):
    url = URL.create(
        "postgresql+psycopg",
        username="datadj_test",
        password=os.environ["DATADJ_TEST_POSTGRES_PASSWORD"],
        host="127.0.0.1",
        port=55432,
        database="datadj_lease_test",
    )
    engine = create_engine(url)
    sessions = create_session_factory(engine)
    service = JobService(sessions, LocalDatasetStorage(tmp_path / "datasets"))

    job_id = uuid4()
    dataset_id = uuid4()
    plan = ExecutionPlan.model_validate({
        "dataset_id": str(dataset_id),
        "observation_count": 5,
        "n_components": 2,
        "random_seed": 42,
        "configs": [{"algorithm": "tsne", "perplexity": 2}],
    })

    try:
        with sessions.begin() as session:
            session.add(
                Job(
                    id=job_id,
                    dataset_id=dataset_id,
                    request_payload={},
                    execution_plan=plan.model_dump(mode="json"),
                )
            )
        claim = service.claim_with_lease(job_id)
        assert claim is not None
        token, _ = claim
        yield engine, sessions, service, job_id, token
    finally:
        with sessions.begin() as session:
            session.execute(delete(Job).where(Job.id == job_id))
        engine.dispose()


def test_checkpoint_publication_fences_stale_workers(checkpoint_job) -> None:
    _, sessions, service, job_id, first_token = checkpoint_job
    identity = "test identity"
    embedding_id = uuid4()

    first = service.publish_checkpoint(job_id, first_token, identity, 1, embedding_id)
    assert first is not None
    assert first.embedding_id == embedding_id
    assert first.blob_key == checkpoint_blob_key(
        job_id, first_token, checkpoint_identity_hash(identity), 1
    )

    assert service.publish_checkpoint(job_id, uuid4(), identity, 2, uuid4()) is None

    with sessions.begin() as session:
        session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                lease_expires_at=func.clock_timestamp()
                - literal(timedelta(seconds=1), type_=Interval())
            )
        )

    assert service.publish_checkpoint(job_id, first_token, identity, 2, uuid4()) is None

    replacement = service.claim_with_lease(job_id)
    assert replacement is not None
    second_token, _ = replacement
    assert second_token != first_token
    assert service.publish_checkpoint(job_id, first_token, identity, 2, uuid4()) is None

    # An existing published result wins over a newly proposed result.
    assert service.publish_checkpoint(job_id, second_token, identity, 1, uuid4()) == first

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                service.publish_checkpoint, job_id, second_token, identity, 3, uuid4()
            )
            for _ in range(2)
        ]
        results = [future.result(timeout=15) for future in futures]

    assert results[0] is not None
    assert results[0] == results[1]

    with sessions.begin() as session:
        assert session.scalar(
            select(func.count()).select_from(JobCheckpoint).where(
                JobCheckpoint.job_id == job_id
            )
        ) == 2

@pytest.mark.parametrize("operation", ["publish", "renew", "success", "failure"])
def test_publication_rechecks_lease_after_waiting_for_lock(
    checkpoint_job, operation: str
) -> None:
    engine, sessions, service, job_id, token = checkpoint_job

    with sessions.begin() as session:
        session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                lease_expires_at=func.clock_timestamp()
                + literal(timedelta(seconds=3), type_=Interval())
            )
        )

    with engine.connect() as lock_connection:
        transaction = lock_connection.begin()
        try:
            holder_pid = lock_connection.scalar(text("SELECT pg_backend_pid()"))
            lock_connection.execute(
                select(Job.id).where(Job.id == job_id).with_for_update()
            ).one()

            with ThreadPoolExecutor(max_workers=1) as pool:
                if operation == "publish":
                    future = pool.submit(
                        service.publish_checkpoint, job_id, token, "identity", 1, uuid4()
                    )
                elif operation == "renew":
                    future = pool.submit(service.renew_lease, job_id, token)
                elif operation == "success":
                    future = pool.submit(
                        service.succeed_with_lease, job_id, token, "exports/result.zip"
                    )
                else:
                    future = pool.submit(
                        service.fail_with_lease, job_id, token, "Failure."
                    )
                try:
                    deadline = time.monotonic() + 10
                    while True:
                        with engine.connect() as probe:
                            blocked = probe.scalar(
                                text(
                                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                    "WHERE :pid = ANY(pg_blocking_pids(pid)))"
                                ),
                                {"pid": holder_pid},
                            )
                        if blocked:
                            break
                        assert time.monotonic() < deadline, "Publisher did not block."
                        time.sleep(0.02)

                    with engine.connect() as probe:
                        assert probe.scalar(
                            select(Job.lease_expires_at > func.clock_timestamp())
                            .where(Job.id == job_id)
                        ), "Lease expired before the lock wait began."

                    deadline = time.monotonic() + 10
                    while True:
                        with engine.connect() as probe:
                            expired = probe.scalar(
                                select(Job.lease_expires_at <= func.clock_timestamp())
                                .where(Job.id == job_id)
                            )
                        if expired:
                            break
                        assert time.monotonic() < deadline, "Lease did not expire."
                        time.sleep(0.02)
                finally:
                    transaction.commit()

                outcome = future.result(timeout=15)
                if operation == "publish":
                    assert outcome is None
                else:
                    assert outcome is False
                with sessions.begin() as session:
                    job = session.get(Job, job_id)
                    assert job is not None
                    assert job.status == JobStatus.RUNNING
                    assert job.claim_token == token
                    assert job.finished_at is None    
        finally:
            if transaction.is_active:
                transaction.rollback()

def test_checkpoint_lookup_requires_current_lease(checkpoint_job) -> None:
    _, sessions, service, job_id, first_token = checkpoint_job
    identity = "test identity"

    assert service.get_published_checkpoint(job_id, first_token, identity, 1) is None

    published = service.publish_checkpoint(job_id, first_token, identity, 1, uuid4())
    assert published is not None
    assert service.get_published_checkpoint(job_id, first_token, identity, 1) == published
    assert service.get_published_checkpoint(job_id, first_token, "different", 1) is None

    with pytest.raises(ValueError, match="positive integer"):
        service.get_published_checkpoint(job_id, first_token, identity, 0)
    with pytest.raises(LeaseLostError):
        service.get_published_checkpoint(job_id, uuid4(), identity, 1)

    with sessions.begin() as session:
        session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                lease_expires_at=func.clock_timestamp()
                - literal(timedelta(seconds=1), type_=Interval())
            )
        )

    with pytest.raises(LeaseLostError):
        service.get_published_checkpoint(job_id, first_token, identity, 1)

    replacement = service.claim_with_lease(job_id)
    assert replacement is not None
    second_token, _ = replacement

    # A replacement can find the earlier attempt's published checkpoint.
    assert service.get_published_checkpoint(job_id, second_token, identity, 1) == published
    with pytest.raises(LeaseLostError):
        service.get_published_checkpoint(job_id, first_token, identity, 1)

    assert service.succeed_with_lease(job_id, second_token, "exports/result.zip")
    with pytest.raises(LeaseLostError):
        service.get_published_checkpoint(job_id, second_token, identity, 1)

def test_replacement_reuses_published_blob_checkpoint(
    checkpoint_job, tmp_path: Path
) -> None:
    _, sessions, jobs, job_id, first_token = checkpoint_job
    storage = LocalDatasetStorage(tmp_path / "datasets")
    dataset = DatasetService().prepare(
        b"value\n1\n2\n3\n4\n5\n", "sample.csv"
    )
    storage.save(dataset)
    plan = ExecutionPlan(
        dataset_id=dataset.metadata.dataset_id,
        observation_count=5,
        n_components=2,
        random_seed=42,
        configs=(TSNEConfig(perplexity=1), TSNEConfig(perplexity=2)),
    )

    with sessions.begin() as session:
        session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                dataset_id=dataset.metadata.dataset_id,
                execution_plan=plan.model_dump(mode="json"),
            )
        )

    objects: dict[str, bytes] = {}

    def get_blob(key: str):
        blob = Mock()

        def download():
            if key not in objects:
                error = ResourceNotFoundError(message="Missing blob.")
                error.error_code = "BlobNotFound"
                raise error
            response = Mock()
            response.readall.return_value = objects[key]
            return response

        def upload(data: bytes, *, overwrite: bool):
            assert overwrite is False
            if key in objects:
                raise ResourceExistsError(message="Already exists.")
            objects[key] = data

        blob.download_blob.side_effect = download
        blob.upload_blob.side_effect = upload
        return blob

    container = Mock(spec=ContainerClient)
    container.get_blob_client.side_effect = get_blob
    batch = BatchService(storage)
    identities: list[str] = []

    def first_factory(identity: str) -> PublishedBlobCheckpoints:
        identities.append(identity)
        return PublishedBlobCheckpoints(container, jobs, job_id, first_token, identity)

    with patch(
        "app.services.embeddings.run_reduction",
        side_effect=[np.zeros((5, 2)), KeyboardInterrupt()],
    ):
        with pytest.raises(KeyboardInterrupt):
            batch.compute_plan_package(
                plan, tmp_path / "exports", checkpoint_factory=first_factory
            )

    with sessions.begin() as session:
        first = session.scalar(
            select(JobCheckpoint).where(
                JobCheckpoint.job_id == job_id,
                JobCheckpoint.run_number == 1,
            )
        )
        assert first is not None
        first_embedding_id = first.embedding_id
        first_blob_key = first.blob_key
        session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                lease_expires_at=func.clock_timestamp()
                - literal(timedelta(seconds=1), type_=Interval())
            )
        )

    replacement = jobs.claim_with_lease(job_id)
    assert replacement is not None
    second_token, _ = replacement

    def second_factory(identity: str) -> PublishedBlobCheckpoints:
        return PublishedBlobCheckpoints(container, jobs, job_id, second_token, identity)

    with patch(
        "app.services.embeddings.run_reduction",
        return_value=np.zeros((5, 2)),
    ) as run:
        package = batch.compute_plan_package(
            plan, tmp_path / "exports", checkpoint_factory=second_factory
        )
        run.assert_called_once()

    with ZipFile(package) as archive:
        manifest = BatchManifest.model_validate_json(archive.read("manifest.json"))
    assert manifest.runs[0].embedding_id == first_embedding_id
    assert len(objects) == 2

    request = EmbeddingRequest(
        dataset_id=dataset.metadata.dataset_id,
        config=plan.configs[0],
        random_seed=plan.random_seed,
    )
    old_store = first_factory(identities[0])
    new_store = second_factory(identities[0])

    with pytest.raises(LeaseLostError):
        old_store.load(1, dataset, request)

    # A published reference to a missing Blob must not look like a cache miss.
    saved_blob = objects.pop(first_blob_key)
    with pytest.raises(FileNotFoundError, match="missing"):
        new_store.load(1, dataset, request)
    objects[first_blob_key] = saved_blob

    with sessions.begin() as session:
        session.execute(
            update(JobCheckpoint)
            .where(
                JobCheckpoint.job_id == job_id,
                JobCheckpoint.run_number == 1,
            )
            .values(embedding_id=uuid4())
        )
    with pytest.raises(ValueError, match="embedding ID"):
        new_store.load(1, dataset, request)

def test_heartbeat_renews_postgres_lease(checkpoint_job) -> None:
    _, sessions, jobs, job_id, token = checkpoint_job

    with sessions.begin() as session:
        session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                lease_expires_at=func.clock_timestamp()
                + literal(timedelta(seconds=30), type_=Interval())
            )
        )

    called = Event()
    proxy = Mock(spec=JobService)

    def renew(current_job_id, current_token):
        try:
            return jobs.renew_lease(current_job_id, current_token)
        finally:
            called.set()

    proxy.renew_lease.side_effect = renew
    heartbeat = LeaseHeartbeat(proxy, job_id, token, interval_seconds=0.01)

    heartbeat.start()
    try:
        assert called.wait(5)
    finally:
        heartbeat.stop(timeout=5)

    heartbeat.raise_if_lost()
    with sessions.begin() as session:
        assert session.scalar(
            select(
                Job.lease_expires_at
                > func.clock_timestamp() + literal(timedelta(minutes=4), type_=Interval())
            ).where(Job.id == job_id)
        )

def test_lease_renewal_times_out_on_blocked_lock(checkpoint_job) -> None:
    engine, _, service, job_id, token = checkpoint_job

    with engine.connect() as connection:
        transaction = connection.begin()
        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                connection.execute(
                    select(Job.id).where(Job.id == job_id).with_for_update()
                ).one()

                future = pool.submit(service.renew_lease, job_id, token)
                with pytest.raises(DBAPIError) as caught:
                    future.result(timeout=12)

                assert caught.value.orig.sqlstate == "55P03"
            finally:
                transaction.rollback()

    # A later renewal works once the blocking transaction is gone.
    assert service.renew_lease(job_id, token)

@pytest.fixture
def cloud_executor_case(checkpoint_job, tmp_path: Path):
    _, sessions, jobs, job_id, _ = checkpoint_job

    storage = LocalDatasetStorage(tmp_path / "executor-datasets")
    dataset = DatasetService().prepare(
        b"value\n1\n2\n3\n4\n5\n", "sample.csv"
    )
    storage.save(dataset)

    plan = ExecutionPlan(
        dataset_id=dataset.metadata.dataset_id,
        observation_count=5,
        n_components=2,
        random_seed=42,
        configs=(TSNEConfig(perplexity=1), TSNEConfig(perplexity=2)),
    )

    # checkpoint_job already claimed this job. Expire that lease so the
    # cloud executor can claim it for this test.
    with sessions.begin() as session:
        session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                dataset_id=dataset.metadata.dataset_id,
                execution_plan=plan.model_dump(mode="json"),
                lease_expires_at=func.clock_timestamp()
                - literal(timedelta(seconds=1), type_=Interval()),
            )
        )

    objects: dict[str, bytes] = {}

    def get_blob(key: str):
        blob = Mock()

        def download():
            if key not in objects:
                error = ResourceNotFoundError(message="Missing blob.")
                error.error_code = "BlobNotFound"
                raise error

            response = Mock()
            response.readall.return_value = objects[key]
            response.chunks.return_value = iter([objects[key]])
            return response

        def upload(data, *, overwrite: bool):
            assert overwrite is False
            if key in objects:
                raise ResourceExistsError(message="Already exists.")

            # Checkpoints upload bytes; exports upload an open file.
            objects[key] = data if isinstance(data, bytes) else data.read()

        blob.download_blob.side_effect = download
        blob.upload_blob.side_effect = upload
        return blob

    container = Mock(spec=ContainerClient)
    container.get_blob_client.side_effect = get_blob

    executor = CloudJobExecutor(jobs, BatchService(storage), container)
    return sessions, jobs, job_id, executor, objects

def test_cloud_executor_resumes_published_checkpoint(cloud_executor_case):
    sessions, jobs, job_id, executor, objects = cloud_executor_case

    # Finish run 1, then simulate an interruption during run 2.
    with patch(
        "app.services.embeddings.run_reduction",
        side_effect=[np.zeros((5, 2)), KeyboardInterrupt()],
    ):
        with pytest.raises(KeyboardInterrupt):
            executor.execute(job_id)

    with sessions.begin() as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.RUNNING
        assert job.artifact_path is None
        first_token = job.claim_token
        assert first_token is not None

        first = session.scalar(
            select(JobCheckpoint).where(
                JobCheckpoint.job_id == job_id,
                JobCheckpoint.run_number == 1,
            )
        )
        assert first is not None
        first_embedding_id = first.embedding_id
        first_blob_key = first.blob_key

        # Simulate the abandoned lease expiring.
        session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                lease_expires_at=func.clock_timestamp()
                - literal(timedelta(seconds=1), type_=Interval())
            )
        )

    assert first_blob_key in objects
    assert not any(key.endswith("/export.zip") for key in objects)

    # The replacement should reuse run 1 and compute only run 2.
    with patch(
        "app.services.embeddings.run_reduction",
        return_value=np.zeros((5, 2)),
    ) as reduction:
        assert executor.execute(job_id) is True
        reduction.assert_called_once()

    with sessions.begin() as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.SUCCEEDED
        assert job.artifact_path is not None
        artifact_key = job.artifact_path

    assert f"/attempts/{first_token}/" not in artifact_key
    assert artifact_key in objects

    with ZipFile(BytesIO(objects[artifact_key])) as archive:
        assert archive.testzip() is None
        manifest = BatchManifest.model_validate_json(archive.read("manifest.json"))

    reader = CompletedBlobResults(jobs, executor.container)
    assert reader.load_manifest(job_id) == manifest
    recovered_result = reader.load_run(job_id, manifest, 1)
    assert recovered_result.embedding_id == first_embedding_id

    assert len(manifest.runs) == 2
    assert manifest.runs[0].embedding_id == first_embedding_id
    assert executor.execute(job_id) is False
    assert not jobs.succeed_with_lease(job_id, first_token, artifact_key)

def test_cloud_executor_does_not_publish_stale_upload(cloud_executor_case):
    sessions, jobs, job_id, executor, objects = cloud_executor_case
    original_upload = executor.exports.upload
    replacement_tokens = []

    def upload_then_replace(upload_job_id, token, package):
        key = original_upload(upload_job_id, token, package)

        # Simulate losing the lease immediately after the upload.
        with sessions.begin() as session:
            session.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    lease_expires_at=func.clock_timestamp()
                    - literal(timedelta(seconds=1), type_=Interval())
                )
            )

        replacement = jobs.claim_with_lease(job_id)
        assert replacement is not None
        assert replacement[0] != token
        replacement_tokens.append(replacement[0])
        return key

    with (
        patch(
            "app.services.embeddings.run_reduction",
            return_value=np.zeros((5, 2)),
        ),
        patch.object(
            executor.exports, "upload", side_effect=upload_then_replace
        ),
    ):
        with pytest.raises(LeaseLostError, match="before success"):
            executor.execute(job_id)

    assert len(replacement_tokens) == 1

    # The uploaded Blob exists, but it was not published as the result.
    assert any(key.endswith("/export.zip") for key in objects)

    with sessions.begin() as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.RUNNING
        assert job.claim_token == replacement_tokens[0]
        assert job.artifact_path is None
        assert job.finished_at is None

def test_postgres_pool_wait_is_bounded(monkeypatch):
    url = URL.create(
        "postgresql+psycopg",
        username="datadj_test",
        password=os.environ["DATADJ_TEST_POSTGRES_PASSWORD"],
        host="127.0.0.1",
        port=55432,
        database="datadj_lease_test",
    )
    monkeypatch.setenv(
        "DATA_DJ_DATABASE_URL", url.render_as_string(hide_password=False)
    )
    engine = create_database_engine()

    try:
        with ExitStack() as stack:
            # SQLAlchemy's current default is 5 pooled + 10 overflow connections.
            for _ in range(15):
                stack.enter_context(engine.connect())

            started = time.monotonic()
            with pytest.raises(PoolTimeout):
                engine.connect()
            elapsed = time.monotonic() - started

            assert 4.5 <= elapsed < 15
    finally:
        engine.dispose()


def test_postgres_connection_handshake_timeout(monkeypatch):
    release = threading.Event()
    accepted = threading.Event()
    errors = []

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)
        port = listener.getsockname()[1]

        def stall_server():
            try:
                connection, _ = listener.accept()
                with connection:
                    accepted.set()
                    release.wait(25)
            except OSError as error:
                errors.append(error)

        thread = threading.Thread(target=stall_server)
        thread.start()

        # This is a local dummy socket, not an Azure database.
        url = URL.create(
            "postgresql+psycopg",
            username="dummy",
            host="127.0.0.1",
            port=port,
            database="dummy",
            query={"connect_timeout": "30"},
        )
        monkeypatch.setenv(
            "DATA_DJ_DATABASE_URL", url.render_as_string(hide_password=False)
        )
        engine = create_database_engine()

        try:
            started = time.monotonic()
            with pytest.raises(OperationalError):
                engine.connect()
            elapsed = time.monotonic() - started

            assert accepted.is_set()
            assert 8 <= elapsed < 20
            assert not errors
        finally:
            release.set()
            thread.join(timeout=5)
            engine.dispose()

        assert not thread.is_alive()

def test_completed_checkpoint_requires_success_and_matching_embedding(checkpoint_job):
    _, _, jobs, job_id, token = checkpoint_job
    embedding_id = uuid4()

    published = jobs.publish_checkpoint(job_id, token, "identity", 1, embedding_id)
    assert published is not None

    # A running job cannot expose completed results.
    assert jobs.get_completed_checkpoint(job_id, 1, embedding_id) is None

    assert jobs.succeed_with_lease(
        job_id, token, f"jobs/{job_id}/attempts/{token}/export.zip"
    )

    assert jobs.get_completed_checkpoint(job_id, 1, embedding_id) == published
    assert jobs.get_completed_checkpoint(job_id, 1, uuid4()) is None
    assert jobs.get_completed_checkpoint(job_id, 2, embedding_id) is None
    assert jobs.get_completed_checkpoint(uuid4(), 1, embedding_id) is None

    with pytest.raises(ValueError, match="positive integer"):
        jobs.get_completed_checkpoint(job_id, 0, embedding_id)


def test_completed_checkpoint_can_reference_prior_attempt(checkpoint_job):
    _, sessions, jobs, job_id, first_token = checkpoint_job
    embedding_id = uuid4()

    published = jobs.publish_checkpoint(
        job_id, first_token, "identity", 1, embedding_id
    )
    assert published is not None

    with sessions.begin() as session:
        session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                lease_expires_at=func.clock_timestamp()
                - literal(timedelta(seconds=1), type_=Interval())
            )
        )

    replacement = jobs.claim_with_lease(job_id)
    assert replacement is not None
    token, _ = replacement
    assert token != first_token

    assert jobs.succeed_with_lease(
        job_id, token, f"jobs/{job_id}/attempts/{token}/export.zip"
    )

    assert jobs.get_completed_checkpoint(job_id, 1, embedding_id) == published


@pytest.mark.parametrize("corruption", ["ambiguous", "invalid_key"])
def test_completed_checkpoint_rejects_bad_reference(checkpoint_job, corruption):
    _, sessions, jobs, job_id, token = checkpoint_job
    embedding_id = uuid4()

    assert jobs.publish_checkpoint(job_id, token, "identity", 1, embedding_id)

    if corruption == "ambiguous":
        assert jobs.publish_checkpoint(
            job_id, token, "other identity", 1, embedding_id
        )
    else:
        with sessions.begin() as session:
            session.execute(
                update(JobCheckpoint)
                .where(JobCheckpoint.job_id == job_id)
                .values(
                    blob_key=(
                        f"jobs/{uuid4()}/attempts/{token}/checkpoints/"
                        + "a" * 64
                        + "/1.json"
                    )
                )
            )

    assert jobs.succeed_with_lease(
        job_id, token, f"jobs/{job_id}/attempts/{token}/export.zip"
    )

    if corruption == "ambiguous":
        with pytest.raises(RuntimeError, match="Multiple published checkpoints"):
            jobs.get_completed_checkpoint(job_id, 1, embedding_id)
    else:
        with pytest.raises(ValueError, match="Invalid completed checkpoint key"):
            jobs.get_completed_checkpoint(job_id, 1, embedding_id)


def test_cloud_result_routes_with_real_postgres_and_owner(cloud_executor_case):
    from fastapi.testclient import TestClient

    from app.api.deps import get_auth_service, get_session_factory
    from app.api.routes.datasets import get_dataset_storage
    from app.main import app
    from app.models.ownership import ResourceOwnership
    from app.models.user import User
    from app.services.auth import AuthService

    sessions, jobs, job_id, executor, _ = cloud_executor_case
    owner, other = uuid4(), uuid4()
    auth = AuthService(sessions, "test-secret-at-least-thirty-two-characters")
    with sessions.begin() as session:
        session.add_all([
            User(id=owner, email=f"{owner}@example.test", password_hash="unused"),
            User(id=other, email=f"{other}@example.test", password_hash="unused"),
        ])
        session.flush()
        session.add(ResourceOwnership(resource_type="job", resource_id=job_id, user_id=owner))

    app.dependency_overrides[get_session_factory] = lambda: sessions
    app.dependency_overrides[get_auth_service] = lambda: auth
    app.dependency_overrides[get_dataset_storage] = lambda: jobs.dataset_storage
    previous_container = app.state.job_blob_container
    app.state.job_blob_container = executor.container
    client = TestClient(app)
    try:
        with patch("app.services.embeddings.run_reduction", return_value=np.zeros((5, 2))):
            assert executor.execute(job_id)
        first = {"Authorization": f"Bearer {auth.create_token(owner)}"}
        second = {"Authorization": f"Bearer {auth.create_token(other)}"}
        catalog = client.get(f"/api/jobs/{job_id}/batch", headers=first)
        assert catalog.status_code == 200, catalog.text
        run = client.get(f"/api/jobs/{job_id}/runs/1", headers=first)
        assert run.status_code == 200, run.text
        assert run.json()["embedding_id"] == catalog.json()["runs"][0]["embedding_id"]
        exported = client.get(f"/api/jobs/{job_id}/export", headers=first)
        assert exported.status_code == 200
        assert exported.content.startswith(b"PK")
        for suffix in ("batch", "runs/1", "export"):
            assert client.get(f"/api/jobs/{job_id}/{suffix}", headers=second).status_code == 404
    finally:
        client.close()
        app.state.job_blob_container = previous_container
        for dependency in (get_session_factory, get_auth_service, get_dataset_storage):
            app.dependency_overrides.pop(dependency, None)
        with sessions.begin() as session:
            session.execute(delete(User).where(User.id.in_((owner, other))))


def test_cloud_worker_skips_unowned_jobs(cloud_executor_case):
    from app.models.ownership import ResourceOwnership
    from app.models.user import User

    sessions, jobs, job_id, _, _ = cloud_executor_case
    owner = uuid4()
    assert jobs.next_leasable(owned_only=True) is None
    try:
        with sessions.begin() as session:
            session.add(User(id=owner, email=f"{owner}@example.test", password_hash="unused"))
            session.flush()
            session.add(ResourceOwnership(
                resource_type="job", resource_id=job_id, user_id=owner
            ))
        assert jobs.next_leasable(owned_only=True) == job_id
    finally:
        with sessions.begin() as session:
            session.execute(delete(User).where(User.id == owner))