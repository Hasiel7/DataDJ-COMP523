from datetime import UTC
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.db import Base, create_database_engine, create_session_factory
from app.models.job import Job, JobStatus
from app.schemas.execution_plan import ExecutionPlan
from app.schemas.grid import GridPreviewRequest
from app.services.datasets import DatasetService
from app.services.embeddings import DatasetNotFoundError
from app.services.jobs import (
    ExecutionPlanUnavailableError,
    JobNotFoundError,
    JobService,
)
from app.storage.local import LocalDatasetStorage


@pytest.mark.parametrize("perplexity", ["2", "5"])
def test_submission_validates_before_inserting(
    tmp_path: Path, perplexity: str
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
                "perplexity": {
                    "start": perplexity, "stop": perplexity, "step": "1"
                }
            }
        },
    })

    try:
        Base.metadata.create_all(engine)
        service = JobService(sessions, storage)
        if perplexity == "5":
            with pytest.raises(ValueError, match="perplexity"):
                service.submit(request)
        else:
            job_id = service.submit(request)
            saved_plan = service.get_execution_plan(job_id)
            assert saved_plan.dataset_id == request.dataset_id
            assert saved_plan.observation_count == 5
            assert len(saved_plan.configs) == 1
            assert saved_plan.configs[0].model_dump()["perplexity"] == 2.0
            summary = service.get(job_id)
            assert summary is not None
            assert summary.job_id == job_id
            assert summary.dataset_id == request.dataset_id
            assert summary.status == JobStatus.QUEUED
            assert summary.created_at.tzinfo == UTC
            assert summary.started_at is None
            assert summary.finished_at is None
            assert service.get(uuid4()) is None
            with sessions.begin() as session:
                job = session.get(Job, job_id)
                assert job is not None
                assert job.status == JobStatus.QUEUED
                assert job.request_payload == request.model_dump(mode="json")
                assert job.execution_plan is not None
                plan = ExecutionPlan.model_validate(job.execution_plan)
                assert plan.dataset_id == request.dataset_id
                assert plan.observation_count == 5
                assert plan.random_seed == request.random_seed
                assert len(plan.configs) == 1

                config = plan.configs[0]
                assert config.algorithm == "tsne"
                assert config.model_dump()["perplexity"] == 2.0
                assert config.model_dump()["max_iter"] == 1000

        with sessions.begin() as session:
            count = session.scalar(select(func.count()).select_from(Job))
            assert count == (0 if perplexity == "5" else 1)
    finally:
        engine.dispose()

def test_missing_dataset_does_not_create_job(tmp_path: Path) -> None:
    engine = create_database_engine(tmp_path / "jobs.sqlite3")
    sessions = create_session_factory(engine)
    storage = LocalDatasetStorage(tmp_path / "datasets")
    request = GridPreviewRequest.model_validate({
        "dataset_id": str(uuid4()),
        "algorithms": {
            "tsne": {
                "perplexity": {"start": "1", "stop": "2", "step": "1"}
            }
        },
    })

    try:
        Base.metadata.create_all(engine)
        service = JobService(sessions, storage)

        with pytest.raises(DatasetNotFoundError, match="Dataset not found"):
            service.submit(request)

        with sessions.begin() as session:
            count = session.scalar(select(func.count()).select_from(Job))
            assert count == 0
    finally:
        engine.dispose()

def test_plan_retrieval_rejects_missing_and_legacy_jobs(tmp_path: Path) -> None:
    engine = create_database_engine(tmp_path / "jobs.sqlite3")
    sessions = create_session_factory(engine)
    storage = LocalDatasetStorage(tmp_path / "datasets")

    try:
        Base.metadata.create_all(engine)
        service = JobService(sessions, storage)

        with pytest.raises(JobNotFoundError, match="Job not found"):
            service.get_execution_plan(uuid4())

        with sessions.begin() as session:
            job = Job(
                dataset_id=uuid4(),
                request_payload={},
                execution_plan=None,
            )
            session.add(job)
            session.flush()
            legacy_job_id = job.id

        with pytest.raises(
            ExecutionPlanUnavailableError,
            match="submit a new job",
        ):
            service.get_execution_plan(legacy_job_id)
    finally:
        engine.dispose()

def test_only_one_service_can_claim_a_queued_job(tmp_path: Path) -> None:
    engine = create_database_engine(tmp_path / "claims.sqlite3")
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
        first = JobService(sessions, storage)
        second = JobService(sessions, storage)
        job_id = first.submit(request)

        plan = first.claim(job_id)
        assert plan == first.get_execution_plan(job_id)
        assert second.claim(job_id) is None
        assert first.claim(uuid4()) is None

        summary = second.get(job_id)
        assert summary is not None
        assert summary.status == JobStatus.RUNNING
        assert summary.started_at is not None
        assert summary.finished_at is None
    finally:
        engine.dispose()

@pytest.mark.parametrize("failure_kind", ["malformed", "wrong_dataset"])
def test_invalid_plan_rolls_back_job_claim(
    tmp_path: Path,
    failure_kind: str,
) -> None:
    engine = create_database_engine(tmp_path / "invalid-plan.sqlite3")
    sessions = create_session_factory(engine)
    storage = LocalDatasetStorage(tmp_path / "datasets")
    dataset_id = uuid4()

    if failure_kind == "malformed":
        payload = {}
        expected_error = ValidationError
        expected_message = "validation error"
    else:
        plan = ExecutionPlan.model_validate({
            "dataset_id": str(uuid4()),
            "observation_count": 5,
            "n_components": 2,
            "random_seed": 42,
            "configs": [{"algorithm": "tsne", "perplexity": 2}],
        })
        payload = plan.model_dump(mode="json")
        expected_error = ValueError
        expected_message = "dataset does not match"

    try:
        Base.metadata.create_all(engine)
        with sessions.begin() as session:
            job = Job(
                dataset_id=dataset_id,
                request_payload={},
                execution_plan=payload,
            )
            session.add(job)
            session.flush()
            job_id = job.id

        service = JobService(sessions, storage)
        with pytest.raises(expected_error, match=expected_message):
            service.claim(job_id)

        with sessions.begin() as session:
            stored = session.get(Job, job_id)
            assert stored is not None
            assert stored.status == JobStatus.QUEUED
            assert stored.started_at is None
            assert stored.finished_at is None
    finally:
        engine.dispose()

@pytest.mark.parametrize("initial_status", list(JobStatus))
@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_completion_only_updates_running_jobs(
    tmp_path: Path,
    initial_status: JobStatus,
    outcome: str,
) -> None:
    engine = create_database_engine(tmp_path / "completion.sqlite3")
    sessions = create_session_factory(engine)
    service = JobService(sessions, LocalDatasetStorage(tmp_path / "datasets"))

    try:
        Base.metadata.create_all(engine)
        with sessions.begin() as session:
            job = Job(
                dataset_id=uuid4(),
                request_payload={},
                status=initial_status,
            )
            session.add(job)
            session.flush()
            job_id = job.id
        for invalid_path in ("", "../escape.zip", "/absolute.zip", "result.csv"):
            with pytest.raises(ValueError, match="relative ZIP path"):
                service.succeed(job_id, invalid_path)

        for invalid_message in ("   ", "x" * 2001):
            with pytest.raises(ValueError, match="2000 characters"):
                service.fail(job_id, invalid_message)

        unchanged = service.get(job_id)
        assert unchanged is not None
        assert unchanged.status == initial_status
        assert unchanged.finished_at is None
        
        if outcome == "success":
            changed = service.succeed(job_id, "exports/result.zip")
            expected = JobStatus.SUCCEEDED
        else:
            changed = service.fail(job_id, "Reduction failed.")
            expected = JobStatus.FAILED

        assert changed == (initial_status == JobStatus.RUNNING)

        with sessions.begin() as session:
            stored = session.get(Job, job_id)
            assert stored is not None
            if changed:
                assert stored.status == expected
                assert stored.finished_at is not None
                assert stored.artifact_path == (
                    "exports/result.zip" if outcome == "success" else None
                )
                assert stored.error_message == (
                    None if outcome == "success" else "Reduction failed."
                )
            else:
                assert stored.status == initial_status
                assert stored.finished_at is None
    finally:
        engine.dispose()

@pytest.mark.parametrize("first_outcome", ["success", "failure"])
def test_first_completion_cannot_be_overwritten(
    tmp_path: Path,
    first_outcome: str,
) -> None:
    engine = create_database_engine(tmp_path / "repeat.sqlite3")
    sessions = create_session_factory(engine)
    service = JobService(sessions, LocalDatasetStorage(tmp_path / "datasets"))

    try:
        Base.metadata.create_all(engine)
        with sessions.begin() as session:
            job = Job(
                dataset_id=uuid4(),
                request_payload={},
                status=JobStatus.RUNNING,
            )
            session.add(job)
            session.flush()
            job_id = job.id

        if first_outcome == "success":
            assert service.succeed(job_id, "exports/original.zip")
        else:
            assert service.fail(job_id, "Original failure.")

        before = service.get(job_id)
        assert not service.succeed(job_id, "exports/replacement.zip")
        assert not service.fail(job_id, "Replacement failure.")
        assert service.get(job_id) == before

        with sessions.begin() as session:
            stored = session.get(Job, job_id)
            assert stored is not None
            assert stored.artifact_path == (
                "exports/original.zip" if first_outcome == "success" else None
            )
            assert stored.error_message == (
                None if first_outcome == "success" else "Original failure."
            )
    finally:
        engine.dispose()