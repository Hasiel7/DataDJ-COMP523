from datetime import UTC, datetime
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

import numpy as np
import pytest
from azure.storage.blob import ContainerClient
from fastapi.testclient import TestClient

from app.api.deps import get_auth_service, get_session_factory
from app.api.routes.batches import get_batch_storage
from app.api.routes.datasets import get_dataset_storage
from app.db import Base, create_database_engine, create_session_factory
from app.dr.config import TSNEConfig
from app.main import app
from app.models.job import Job, JobCheckpoint, JobStatus
from app.models.ownership import ResourceOwnership
from app.models.user import User
from app.schemas.batch import BatchManifest, BatchRunRecord
from app.schemas.embedding import EmbeddingRequest
from app.services.auth import AuthService
from app.services.datasets import DatasetService
from app.services.embeddings import EmbeddingService
from app.storage.batches import LocalBatchStorage
from app.storage.checkpoints import checkpoint_blob_key, checkpoint_identity_hash, encode_checkpoint
from app.storage.local import LocalDatasetStorage


@pytest.fixture
def authorized_api(tmp_path):
    engine = create_database_engine(tmp_path / "authz.sqlite3")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    storage = LocalDatasetStorage(tmp_path / "datasets")
    batches = LocalBatchStorage(tmp_path / "batches")
    auth = AuthService(sessions, "test-secret-not-for-production-at-least-32-bytes")
    first, second = uuid4(), uuid4()
    with sessions.begin() as session:
        session.add_all([
            User(id=first, email="first@example.com", password_hash="unused"),
            User(id=second, email="second@example.com", password_hash="unused"),
        ])
    headers = [
        {"Authorization": f"Bearer {auth.create_token(user_id)}"}
        for user_id in (first, second)
    ]
    app.dependency_overrides[get_session_factory] = lambda: sessions
    app.dependency_overrides[get_auth_service] = lambda: auth
    app.dependency_overrides[get_dataset_storage] = lambda: storage
    app.dependency_overrides[get_batch_storage] = lambda: batches
    client = TestClient(app)
    try:
        yield client, headers, storage
    finally:
        client.close()
        for dep in (get_session_factory, get_auth_service, get_dataset_storage, get_batch_storage):
            app.dependency_overrides.pop(dep, None)
        engine.dispose()


def test_dataset_job_and_legacy_access(authorized_api):
    client, (first, second), storage = authorized_api
    content = b"value\n1\n2\n3\n"
    assert client.post("/api/datasets", files={"file": ("sample.csv", content)}).status_code == 401
    uploaded = client.post(
        "/api/datasets", headers=first, files={"file": ("sample.csv", content)}
    )
    assert uploaded.status_code == 201, uploaded.text
    dataset_id = uploaded.json()["dataset_id"]
    assert client.get(f"/api/datasets/{dataset_id}", headers=first).status_code == 200
    assert client.get(f"/api/datasets/{dataset_id}", headers=second).status_code == 404

    legacy = DatasetService().prepare(content, "legacy.csv")
    storage.save(legacy)
    legacy_response = client.get(f"/api/datasets/{legacy.metadata.dataset_id}", headers=first)
    assert legacy_response.status_code == 404

    request = {
        "dataset_id": dataset_id,
        "algorithms": {"tsne": {"perplexity": {"start": "1", "stop": "1", "step": "1"}}},
    }
    assert client.post("/api/grids/preview", headers=second, json=request).status_code == 404
    assert client.post("/api/jobs", headers=second, json=request).status_code == 404
    submitted = client.post("/api/jobs", headers=first, json=request)
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["job_id"]
    assert client.get(f"/api/jobs/{job_id}", headers=first).status_code == 200
    assert client.get(f"/api/jobs/{job_id}", headers=second).status_code == 404
    assert client.get(f"/api/jobs/{job_id}/batch", headers=second).status_code == 404
    assert client.get(f"/api/jobs/{job_id}/runs/1", headers=second).status_code == 404


def test_imported_batch_is_private(authorized_api):
    client, (first, second), _ = authorized_api
    content = (
        b"run_number,index,method,label,dim1,dim2,umap_n_neighbors\n"
        b"1,a,umap,x,1,2,2\n"
        b"1,b,umap,y,3,4,2\n"
    )
    response = client.post(
        "/api/batches/import", headers=first, files={"file": ("batch.csv", content)}
    )
    assert response.status_code == 201, response.text
    batch_id = response.json()["batch_id"]
    assert client.get(f"/api/batches/{batch_id}", headers=first).status_code == 200
    assert client.get(f"/api/batches/{batch_id}/runs/1", headers=first).status_code == 200
    assert client.get(f"/api/batches/{batch_id}", headers=second).status_code == 404
    assert client.get(f"/api/batches/{batch_id}/runs/1", headers=second).status_code == 404


def test_owned_cloud_result_and_export(authorized_api):
    client, (first, second), storage = authorized_api
    upload = client.post(
        "/api/datasets", headers=first, files={"file": ("sample.csv", b"value\n1\n2\n3\n")}
    )
    assert upload.status_code == 201
    dataset = storage.get(UUID(upload.json()["dataset_id"]))
    assert dataset is not None
    config = TSNEConfig(perplexity=1)
    request = EmbeddingRequest(
        dataset_id=dataset.metadata.dataset_id, config=config, random_seed=42
    )
    with patch("app.services.embeddings.run_reduction", return_value=np.zeros((3, 2))):
        result = EmbeddingService.compute_prepared(dataset, request)
    manifest = BatchManifest(
        batch_id=uuid4(), created_at=datetime.now(UTC), dataset=dataset.metadata,
        random_seed=42, n_components=2, csv_filename="embeddings.csv", package_versions={},
        runs=[BatchRunRecord(
            run_number=1, embedding_id=result.embedding_id, config=config,
            observation_count=3, execution_time_seconds=result.execution_time_seconds,
        )],
    )
    job_id, token = uuid4(), uuid4()
    artifact = f"jobs/{job_id}/attempts/{token}/export.zip"
    identity_hash = checkpoint_identity_hash("identity")
    checkpoint = checkpoint_blob_key(job_id, token, identity_hash, 1)
    sessions = app.dependency_overrides[get_session_factory]()
    auth = app.dependency_overrides[get_auth_service]()
    owner = auth.user_for_token(first["Authorization"].removeprefix("Bearer ")).id
    with sessions.begin() as session:
        session.add(Job(
            id=job_id, dataset_id=dataset.metadata.dataset_id, status=JobStatus.SUCCEEDED,
            request_payload={}, execution_plan={}, artifact_path=artifact,
        ))
        session.flush()
        session.add_all([
            ResourceOwnership(resource_type="job", resource_id=job_id, user_id=owner),
            JobCheckpoint(
                job_id=job_id, identity_hash=identity_hash, run_number=1,
                blob_key=checkpoint, embedding_id=result.embedding_id,
            ),
        ])

    objects = {
        f"jobs/{job_id}/attempts/{token}/manifest.json": manifest.model_dump_json().encode(),
        checkpoint: encode_checkpoint(result).encode(),
        artifact: b"test zip stream",
    }
    container = Mock(spec=ContainerClient)

    def get_blob(key):
        blob = Mock()
        blob.download_blob.return_value.readall.return_value = objects[key]
        blob.download_blob.return_value.chunks.return_value = iter([objects[key]])
        return blob

    container.get_blob_client.side_effect = get_blob
    previous = app.state.job_blob_container
    app.state.job_blob_container = container
    try:
        assert client.get(f"/api/jobs/{job_id}/batch", headers=first).status_code == 200
        response = client.get(f"/api/jobs/{job_id}/runs/1", headers=first)
        assert response.status_code == 200
        assert response.json()["embedding_id"] == str(result.embedding_id)
        export = client.get(f"/api/jobs/{job_id}/export", headers=first)
        assert export.status_code == 200
        assert export.content == b"test zip stream"
        assert client.get(f"/api/jobs/{job_id}/export", headers=second).status_code == 404
        assert client.get(f"/api/jobs/{job_id}/batch", headers=second).status_code == 404
        objects[checkpoint] = b"bad"
        assert client.get(f"/api/jobs/{job_id}/runs/1", headers=first).status_code == 503
    finally:
        app.state.job_blob_container = previous
