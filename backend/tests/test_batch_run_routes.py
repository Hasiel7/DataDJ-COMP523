import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient

from app.api.routes.batches import get_batch_storage
from app.api.routes.jobs import get_job_service
from app.db import Base, create_database_engine, create_session_factory
from app.dr.config import Algorithm, TSNEConfig
from app.main import app
from app.models.job import Job, JobStatus
from app.schemas.batch import BatchManifest, BatchRunRecord
from app.schemas.embedding import EmbeddingResult
from app.services.datasets import DatasetService
from app.services.jobs import JobService
from app.storage.batches import LocalBatchStorage
from app.storage.local import LocalDatasetStorage


@pytest.fixture
def batch_api(tmp_path: Path):
    engine = create_database_engine(tmp_path / "jobs.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    storage = LocalDatasetStorage(tmp_path / "datasets")
    dataset = DatasetService().prepare(b"value\n1\n2\n3\n", "sample.csv")
    storage.save(dataset)
    jobs = JobService(sessions, storage)
    job_id = uuid4()
    config = TSNEConfig(perplexity=1)
    result = EmbeddingResult(
        embedding_id=uuid4(),
        dataset_id=dataset.metadata.dataset_id,
        algorithm=Algorithm.TSNE,
        hyperparameters=config.model_dump(exclude={"algorithm"}),
        random_seed=42,
        observation_count=3,
        output_dimensions=2,
        execution_time_seconds=1.25,
        row_ids=["a", "b", "c"],
        coordinates=[[0.0, 1.0], [1.0, 0.0], [-1.0, 0.5]],
        labels=["x", "y", "x"],
    )
    now = datetime.now(UTC)
    pending_job_id = uuid4()
    manifest = BatchManifest(
        batch_id=uuid4(),
        created_at=now,
        dataset=dataset.metadata,
        random_seed=42,
        n_components=2,
        csv_filename="embeddings.csv",
        package_versions={},
        runs=[
            BatchRunRecord(
                run_number=1,
                embedding_id=result.embedding_id,
                config=config,
                observation_count=3,
                execution_time_seconds=1.25,
            )
        ],
    )
    artifact_root = tmp_path / "artifacts"
    checkpoint_directory = artifact_root / "checkpoints" / str(job_id) / "identity"
    checkpoint_directory.mkdir(parents=True)
    payload = result.model_dump_json()
    (checkpoint_directory / "1.json").write_text(
        json.dumps(
            {
                "result": payload,
                "sha256": hashlib.sha256(payload.encode()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    exports = artifact_root / "exports"
    exports.mkdir(parents=True)
    package = exports / "batch-test.zip"
    with ZipFile(package, "w", ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest.model_dump_json())
        archive.writestr("embeddings.csv", "index,dim1,dim2\n")
    with sessions.begin() as session:
        session.add(
            Job(
                id=job_id,
                dataset_id=dataset.metadata.dataset_id,
                status=JobStatus.SUCCEEDED,
                request_payload={},
                execution_plan={},
                artifact_path="exports/batch-test.zip",
                started_at=now,
                finished_at=now,
            )
        )
        session.add(
            Job(
                id=pending_job_id,
                dataset_id=dataset.metadata.dataset_id,
                status=JobStatus.QUEUED,
                request_payload={},
                execution_plan={},
            )
        )
    previous_root = app.state.artifact_root
    app.state.artifact_root = artifact_root
    app.dependency_overrides[get_job_service] = lambda: jobs
    client = TestClient(app)
    try:
        yield client, job_id, result, checkpoint_directory, pending_job_id
    finally:
        app.dependency_overrides.pop(get_job_service, None)
        app.state.artifact_root = previous_root
        client.close()
        engine.dispose()


def test_batch_catalog_returns_manifest_and_run_endpoint_returns_one_result(batch_api) -> None:
    client, job_id, result, _, _ = batch_api
    catalog = client.get(f"/api/jobs/{job_id}/batch")
    assert catalog.status_code == 200, catalog.text
    assert len(catalog.json()["runs"]) == 1
    response = client.get(f"/api/jobs/{job_id}/runs/1")
    assert response.status_code == 200, response.text
    assert response.json()["embedding_id"] == str(result.embedding_id)
    assert response.json()["coordinates"] == result.coordinates
    assert len(response.json()["coordinates"]) == 3


def test_batch_endpoints_reject_pending_jobs_and_unknown_runs(batch_api) -> None:
    client, job_id, _, _, pending_job_id = batch_api
    pending = client.get(f"/api/jobs/{pending_job_id}/batch")
    assert pending.status_code == 409
    response = client.get(f"/api/jobs/{job_id}/runs/2")
    assert response.status_code == 404


def test_batch_run_endpoint_rejects_corrupt_checkpoint(batch_api) -> None:
    client, job_id, _, checkpoint_directory, _ = batch_api
    (checkpoint_directory / "1.json").write_text("{}", encoding="utf-8")
    response = client.get(f"/api/jobs/{job_id}/runs/1")
    assert response.status_code == 404


def test_streaming_batch_import_endpoint_returns_catalog_and_run(tmp_path: Path) -> None:
    batch_storage = LocalBatchStorage(tmp_path / "imported-batches")
    app.dependency_overrides[get_batch_storage] = lambda: batch_storage
    content = (
        b"run_number,index,method,label,dim1,dim2,umap_n_neighbors,umap_min_dist,umap_n_epochs\n"
        b"1,a,umap,x,1,2,5,0.1,200\n"
        b"1,b,umap,y,3,4,5,0.1,200\n"
    )
    try:
        with TestClient(app) as client:
            imported = client.post(
                "/api/batches/import",
                files={"file": ("research.csv", content, "text/csv")},
            )
            assert imported.status_code == 201, imported.text
            catalog = imported.json()
            assert catalog["filename"] == "research.csv"
            assert catalog["observation_count"] == 2
            batch_id = catalog["batch_id"]
            fetched_catalog = client.get(f"/api/batches/{batch_id}")
            assert fetched_catalog.status_code == 200
            run = client.get(f"/api/batches/{batch_id}/runs/1")
    finally:
        app.dependency_overrides.pop(get_batch_storage, None)
    assert run.status_code == 200, run.text
    assert run.json()["coordinates"] == [[1.0, 2.0], [3.0, 4.0]]


def test_batch_import_enforces_upload_limit_before_indexing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.routes import batches as batch_routes

    storage = LocalBatchStorage(tmp_path / "limited-batches")
    monkeypatch.setattr(batch_routes, "MAX_BATCH_UPLOAD_BYTES", 1)
    app.dependency_overrides[get_batch_storage] = lambda: storage
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/batches/import",
                files={"file": ("too-large.csv", b"index\n", "text/csv")},
            )
    finally:
        app.dependency_overrides.pop(get_batch_storage, None)
    assert response.status_code == 413
    assert not list((tmp_path / "limited-batches").glob("*/catalog.json"))
