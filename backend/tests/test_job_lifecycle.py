from pathlib import Path
from unittest.mock import patch
from uuid import UUID
from zipfile import ZipFile

import numpy as np
from fastapi.testclient import TestClient

from app.api.routes.jobs import get_job_service
from app.db import Base, create_database_engine, create_session_factory
from app.main import app
from app.models.job import JobStatus
from app.services.batches import BatchService
from app.services.datasets import DatasetService
from app.services.job_executor import JobExecutor
from app.services.jobs import JobService
from app.storage.local import LocalDatasetStorage


def test_api_submission_worker_execution_and_status(tmp_path: Path) -> None:
    engine = create_database_engine(tmp_path / "jobs.sqlite3")
    sessions = create_session_factory(engine)
    datasets = LocalDatasetStorage(tmp_path / "datasets")
    dataset = DatasetService().prepare(
        b"value\n1\n2\n3\n4\n5\n", "sample.csv"
    )
    datasets.save(dataset)
    Base.metadata.create_all(engine)

    jobs = JobService(sessions, datasets)
    app.dependency_overrides[get_job_service] = lambda: jobs
    client = TestClient(app)
    artifact_root = tmp_path / "artifacts"
    executor = JobExecutor(jobs, BatchService(datasets), artifact_root)

    try:
        submitted = client.post(
            "/api/jobs",
            json={
                "dataset_id": str(dataset.metadata.dataset_id),
                "algorithms": {
                    "tsne": {
                        "perplexity": {
                            "start": "2",
                            "stop": "2",
                            "step": "1",
                        }
                    }
                },
            },
        )
        assert submitted.status_code == 202, submitted.text
        job_id = UUID(submitted.json()["job_id"])
        assert submitted.json()["status"] == "queued"

        with patch(
            "app.services.embeddings.run_reduction",
            return_value=np.zeros((5, 2)),
        ) as reduction:
            assert executor.execute(job_id) is True
            reduction.assert_called_once()

        status = client.get(f"/api/jobs/{job_id}")
        assert status.status_code == 200, status.text
        body = status.json()
        assert body["status"] == JobStatus.SUCCEEDED.value
        assert body["started_at"] is not None
        assert body["finished_at"] is not None
        assert body["artifact_path"].startswith("exports/")

        package = artifact_root / body["artifact_path"]
        assert package.is_file()
        with ZipFile(package) as archive:
            assert archive.testzip() is None
            assert set(archive.namelist()) == {
                "embeddings.csv",
                "manifest.json",
            }
    finally:
        app.dependency_overrides.pop(get_job_service, None)
        client.close()
        engine.dispose()