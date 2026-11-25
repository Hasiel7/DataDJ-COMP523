import os
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from alembic.config import Config
from azure.core import MatchConditions
from azure.core.exceptions import ResourceModifiedError
from azure.storage.blob import UserDelegationKey
from fastapi.testclient import TestClient
from sqlalchemy import URL, create_engine, inspect, text, update

from alembic import command
from app.api.deps import get_current_user
from app.api.routes.batches import direct_imports
from app.db import create_session_factory
from app.main import app
from app.models.batch_import import BatchImport
from app.models.ownership import ResourceOwnership
from app.models.user import User
from app.schemas.auth import UserPublic
from app.services.batch_imports import UPLOAD_LIMIT, BatchImports, upload_key
from app.services.jobs import LeaseLostError

CSV = b"run_number,index,method,dim1,dim2\n1,a,tsne,1,2\n1,b,tsne,3,4\n"


@pytest.fixture
def postgres_engine():
    password = os.environ.get("DATADJ_TEST_POSTGRES_PASSWORD")
    if not password:
        pytest.skip("Disposable PostgreSQL password is not configured")
    url = URL.create(
        "postgresql+psycopg", username="datadj_test", password=password,
        host="127.0.0.1", port=55432, database="datadj_lease_test",
    )
    admin = create_engine(url)
    schema = f"batch_import_test_{uuid4().hex}"
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def postgres_imports(postgres_engine):
    User.__table__.create(postgres_engine)
    BatchImport.__table__.create(postgres_engine)
    ResourceOwnership.__table__.create(postgres_engine)
    sessions = create_session_factory(postgres_engine)
    owner = uuid4()
    with sessions.begin() as session:
        session.add(User(id=owner, email=f"{owner}@example.com", password_hash="hash"))
    blobs = Mock()
    container = blobs.get_container_client.return_value
    return BatchImports(sessions, blobs), owner, container


def test_import_migration_upgrades_and_downgrades_postgres(postgres_engine):
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    with postgres_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        assert "batch_imports" in inspect(connection).get_table_names()
        command.downgrade(config, "c87401d2a161")
        assert "batch_imports" not in inspect(connection).get_table_names()
        assert "resource_ownership" in inspect(connection).get_table_names()


def insert_upload(service, owner, *, status="uploading"):
    import_id = uuid4()
    now = datetime.now(UTC)
    with service.sessions.begin() as session:
        session.add(BatchImport(
            id=import_id, user_id=owner, filename="data.csv", status=status,
            created_at=now, upload_expires_at=now + timedelta(hours=2),
            etag='"original"' if status == "queued" else None,
        ))
    return import_id


def test_complete_checks_actual_size_and_owner(postgres_imports):
    service, owner, container = postgres_imports
    item_id = insert_upload(service, owner)
    blob = container.get_blob_client.return_value
    blob.get_blob_properties.return_value = Mock(size=UPLOAD_LIMIT + 1, etag='"v1"')
    assert service.complete_upload(item_id, uuid4()) is None
    blob.get_blob_properties.assert_not_called()
    item = service.complete_upload(item_id, owner)
    assert item.status == "failed"
    assert item.error_message
    blob.delete_blob.assert_called_once()

    item_id = insert_upload(service, owner)
    blob.get_blob_properties.return_value = Mock(size=123, etag='"v2"')
    assert service.complete_upload(item_id, owner).status == "queued"
    assert service.complete_upload(item_id, owner).etag == '"v2"'
    assert service.get(item_id, uuid4()) is None


def test_import_lease_contention_recovery_and_fenced_publication(postgres_imports):
    service, owner, _ = postgres_imports
    item_id = insert_upload(service, owner, status="queued")
    barrier = Barrier(2)

    def claim():
        barrier.wait(timeout=10)
        return service.claim(item_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim(), range(2)))
    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    stale = winners[0][0]
    assert service.renew_lease(item_id, stale)
    with service.sessions.begin() as session:
        session.execute(
            update(BatchImport).where(BatchImport.id == item_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert not service.renew_lease(item_id, stale)
    replacement = service.claim(item_id)
    assert replacement is not None
    batch_id = uuid4()
    assert not service.finish(item_id, stale, batch_id=batch_id, error=None)
    assert service.finish(item_id, replacement[0], batch_id=batch_id, error=None)
    with service.sessions() as session:
        assert session.get(ResourceOwnership, ("batch", batch_id)).user_id == owner
        assert session.get(BatchImport, item_id).status == "succeeded"


def test_worker_publishes_only_unchanged_source(postgres_imports):
    service, owner, container = postgres_imports
    item_id = insert_upload(service, owner, status="queued")
    objects = {}
    source = Mock()
    source.download_blob.return_value.readinto.side_effect = lambda stream: stream.write(CSV)

    def blob_for(key):
        if key == upload_key(item_id):
            return source
        blob = Mock()
        blob.upload_blob.side_effect = (
            lambda stream, overwrite: objects.update({key: stream.read()})
        )
        return blob

    container.get_blob_client.side_effect = blob_for
    assert service.execute(item_id)
    source.download_blob.assert_called_once_with(
        etag='"original"', match_condition=MatchConditions.IfNotModified
    )
    source.delete_blob.assert_called_once()
    item = service.get(item_id, owner)
    assert item.status == "succeeded"
    assert f"batches/{item.batch_id}/catalog.json" in objects
    with service.sessions() as session:
        assert session.get(ResourceOwnership, ("batch", item.batch_id)).user_id == owner


def test_worker_rejects_changed_source(postgres_imports):
    service, owner, container = postgres_imports
    item_id = insert_upload(service, owner, status="queued")
    blob = container.get_blob_client.return_value
    blob.download_blob.side_effect = ResourceModifiedError(message="changed")
    with pytest.raises(ResourceModifiedError):
        service.execute(item_id)
    item = service.get(item_id, owner)
    assert item.status == "failed"
    assert item.batch_id is None
    blob.delete_blob.assert_called_once()


def test_lost_lease_never_deletes_or_publishes_source(postgres_imports):
    service, owner, container = postgres_imports
    item_id = insert_upload(service, owner, status="queued")
    blob = container.get_blob_client.return_value
    blob.download_blob.side_effect = LeaseLostError("lost")
    with pytest.raises(LeaseLostError):
        service.execute(item_id)
    assert service.get(item_id, owner).status == "running"
    blob.delete_blob.assert_not_called()


def test_heartbeat_shutdown_failure_never_publishes(postgres_imports):
    service, owner, container = postgres_imports
    item_id = insert_upload(service, owner, status="queued")
    heartbeat = Mock()
    heartbeat.stop.side_effect = TimeoutError("shutdown timed out")
    heartbeat.raise_if_lost.side_effect = LeaseLostError("uncertain")
    container.get_blob_client.return_value.download_blob.side_effect = RuntimeError("network")
    with patch("app.services.batch_imports.LeaseHeartbeat", return_value=heartbeat):
        with pytest.raises(LeaseLostError):
            service.execute(item_id)
    assert service.get(item_id, owner).status == "running"
    container.get_blob_client.return_value.delete_blob.assert_not_called()


def test_import_routes_do_not_disclose_other_users_imports():
    user = UserPublic(id=uuid4(), email="user@example.com", created_at=datetime.now(UTC))
    imports = Mock()
    imports.get.return_value = None
    imports.complete_upload.return_value = None
    imports.create.return_value = (Mock(id=uuid4()), "https://example.test/blob?token")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[direct_imports] = lambda: imports
    try:
        with TestClient(app, raise_server_exceptions=True) as client:
            item_id = uuid4()
            assert client.get(f"/api/batch-imports/{item_id}").status_code == 404
            assert client.post(f"/api/batch-imports/{item_id}/complete").status_code == 404
            assert client.post(
                "/api/batch-imports", json={"filename": "x.csv", "size": UPLOAD_LIMIT + 1}
            ).status_code == 422
            imports.create.assert_not_called()
            response = client.post(
                "/api/batch-imports", json={"filename": "x.csv", "size": 1}
            )
            assert response.status_code == 201
            assert response.headers["cache-control"] == "no-store"
            assert response.json()["upload_url"].startswith("https://")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(direct_imports, None)


def test_create_returns_blob_scoped_write_only_token(postgres_imports):
    service, owner, container = postgres_imports
    service.blobs.account_name = "example"
    container.get_blob_client.return_value.url = "https://example.blob.core.windows.net/datadj/blob"
    with patch("app.services.batch_imports.generate_blob_sas", return_value="token") as sas:
        item, url = service.create(owner, "../data.csv")
    assert item.filename == "data.csv"
    assert url.endswith("?token")
    assert sas.call_args.kwargs["blob_name"] == upload_key(item.id)
    permissions = sas.call_args.kwargs["permission"]
    assert permissions.create and permissions.write
    assert not permissions.read and not permissions.delete
    assert sas.call_args.kwargs["protocol"] == "https"


def test_sdk_signs_a_blob_scoped_delegation_token(postgres_imports):
    service, owner, container = postgres_imports
    service.blobs.account_name = "example"
    container.get_blob_client.return_value.url = "https://example.blob.core.windows.net/datadj/blob"
    key = UserDelegationKey()
    key.signed_oid = str(uuid4())
    key.signed_tid = str(uuid4())
    key.signed_start = "2026-01-01T00:00:00Z"
    key.signed_expiry = "2027-01-01T00:00:00Z"
    key.signed_service = "b"
    key.signed_version = "2021-12-02"
    key.value = b64encode(b"test key").decode()
    service.blobs.get_user_delegation_key.return_value = key
    _, url = service.create(owner, "data.csv")
    values = parse_qs(urlsplit(url).query)
    assert values["sr"] == ["b"]
    assert set(values["sp"][0]) == {"c", "w"}
    assert values["spr"] == ["https"]
    assert values["sig"][0]
