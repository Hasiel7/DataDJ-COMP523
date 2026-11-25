import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI

from app.main import lifespan
from app.storage.blob_batches import BlobBatchStorage
from app.storage.blob_datasets import BlobDatasetStorage


def test_blob_api_wires_shared_storage_and_closes_resources(monkeypatch):
    monkeypatch.setenv("DATA_DJ_DATASET_STORAGE", "blob")
    monkeypatch.setenv("DATA_DJ_JOB_STORAGE", "blob")
    monkeypatch.setenv("DATA_DJ_DATABASE_URL", "postgresql+psycopg://localhost/test")
    monkeypatch.setenv("DATA_DJ_BLOB_ACCOUNT_URL", "https://example.blob.core.windows.net")
    monkeypatch.setenv("DATA_DJ_JWT_SECRET", "test-secret-at-least-thirty-two-characters")
    engine = MagicMock()
    credential = MagicMock()
    service = MagicMock()
    container = service.__enter__.return_value.get_container_client.return_value
    application = FastAPI()

    async def run():
        async with lifespan(application):
            assert isinstance(application.state.dataset_storage, BlobDatasetStorage)
            assert isinstance(application.state.batch_storage, BlobBatchStorage)
            assert application.state.job_blob_container is container

    with (
        patch("app.main.create_database_engine", return_value=engine),
        patch("app.main.ManagedIdentityCredential", return_value=credential),
        patch("app.main.BlobServiceClient", return_value=service),
    ):
        asyncio.run(run())
    engine.dispose.assert_called_once()
    service.__exit__.assert_called_once()
    credential.__exit__.assert_called_once()


def test_blob_jobs_require_shared_datasets(monkeypatch):
    monkeypatch.setenv("DATA_DJ_DATASET_STORAGE", "local")
    monkeypatch.setenv("DATA_DJ_JOB_STORAGE", "blob")
    application = FastAPI()
    engine = MagicMock()

    async def run():
        async with lifespan(application):
            raise AssertionError("Invalid configuration must not start")

    with patch("app.main.create_database_engine", return_value=engine):
        with pytest.raises(ValueError, match="requires Blob dataset storage"):
            asyncio.run(run())
    engine.dispose.assert_called_once()


def test_shared_storage_rejects_missing_jwt_secret(monkeypatch):
    monkeypatch.setenv("DATA_DJ_DATASET_STORAGE", "blob")
    monkeypatch.setenv("DATA_DJ_JOB_STORAGE", "local")
    monkeypatch.delenv("DATA_DJ_JWT_SECRET", raising=False)
    application = FastAPI()
    engine = MagicMock()

    async def run():
        async with lifespan(application):
            raise AssertionError("Invalid configuration must not start")

    with patch("app.main.create_database_engine", return_value=engine):
        with pytest.raises(ValueError, match="DATA_DJ_JWT_SECRET"):
            asyncio.run(run())
    engine.dispose.assert_called_once()
