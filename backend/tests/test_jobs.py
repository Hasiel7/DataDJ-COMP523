from pathlib import Path

from app.db import Base, create_database_engine, create_session_factory
from app.models.job import Job, JobStatus
from app.schemas.grid import GridPreviewRequest


def test_job_persists_validated_request(tmp_path: Path) -> None:
    from uuid import uuid4

    engine = create_database_engine(tmp_path / "jobs.sqlite3")
    session_factory = create_session_factory(engine)
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

        with session_factory.begin() as session:
            job = Job(
                dataset_id=request.dataset_id,
                request_payload=request.model_dump(mode="json"),
            )
            session.add(job)
            session.flush()
            job_id = job.id

        with session_factory.begin() as session:
            stored = session.get(Job, job_id)
            assert stored is not None
            assert stored.status == JobStatus.QUEUED
            assert stored.dataset_id == request.dataset_id
            assert stored.artifact_path is None
            assert GridPreviewRequest.model_validate(
                stored.request_payload
            ) == request
    finally:
        engine.dispose()