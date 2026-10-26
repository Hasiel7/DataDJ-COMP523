from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes.jobs import get_job_service
from app.db import Base, create_database_engine, create_session_factory
from app.main import app
from app.models.job import Job
from app.services.datasets import DatasetService
from app.services.job_executor import JobExecutor
from app.services.jobs import JobService
from app.storage.local import LocalDatasetStorage


@dataclass
class JobApi:
    client: TestClient
    sessions: sessionmaker[Session]
    dataset_id: UUID

    def job_count(self) -> int:
        with self.sessions.begin() as session:
            return session.scalar(select(func.count()).select_from(Job)) or 0


def _payload(dataset_id: UUID, perplexity: str = "2") -> dict:
    return {
        "dataset_id": str(dataset_id),
        "algorithms": {
            "tsne": {
                "perplexity": {"start": perplexity, "stop": perplexity, "step": "1"}
            }
        },
    }


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[JobApi]:
    engine = create_database_engine(tmp_path / "jobs.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    storage = LocalDatasetStorage(tmp_path / "datasets")
    dataset = DatasetService().prepare(b"value\n1\n2\n3\n4\n5\n", "sample.csv")
    storage.save(dataset)

    def fail_execute(self: JobExecutor, job_id: UUID) -> bool:
        raise AssertionError("Job submission must not invoke the executor.")

    monkeypatch.setattr(JobExecutor, "execute", fail_execute)
    app.dependency_overrides[get_job_service] = lambda: JobService(sessions, storage)

    try:
        # No context manager: skip lifespan so the real database is never opened.
        yield JobApi(TestClient(app), sessions, dataset.metadata.dataset_id)
    finally:
        app.dependency_overrides.pop(get_job_service, None)
        engine.dispose()


def test_valid_grid_is_queued_without_execution(api: JobApi) -> None:
    response = api.client.post("/api/jobs", json=_payload(api.dataset_id))

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued"
    job_id = UUID(body["job_id"])
    assert api.job_count() == 1
    with api.sessions.begin() as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.started_at is None


def test_invalid_grid_returns_422_without_creating_job(api: JobApi) -> None:
    response = api.client.post(
        "/api/jobs", json=_payload(api.dataset_id, perplexity="5")
    )

    assert response.status_code == 422, response.text
    assert "perplexity" in response.text
    assert api.job_count() == 0


def test_unknown_dataset_returns_404(api: JobApi) -> None:
    response = api.client.post("/api/jobs", json=_payload(uuid4()))

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Dataset not found."
    assert api.job_count() == 0


def test_unknown_job_returns_404(api: JobApi) -> None:
    response = api.client.get(f"/api/jobs/{uuid4()}")

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Job not found."


def test_submitted_job_is_retrievable_as_queued(api: JobApi) -> None:
    submitted = api.client.post("/api/jobs", json=_payload(api.dataset_id))
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["job_id"]

    response = api.client.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job_id"] == job_id
    assert body["dataset_id"] == str(api.dataset_id)
    assert body["status"] == "queued"
    assert body["artifact_path"] is None
    assert body["started_at"] is None
    assert body["finished_at"] is None
