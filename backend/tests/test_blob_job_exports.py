from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import ContainerClient

from app.dr.config import TSNEConfig
from app.schemas.batch import BatchManifest, BatchRunRecord
from app.services.datasets import DatasetService
from app.storage.blob_job_exports import BlobJobExports, manifest_blob_key


@pytest.fixture
def manifest():
    dataset = DatasetService().prepare(b"value\n1\n2\n3\n", "sample.csv")
    config = TSNEConfig(perplexity=1)

    return BatchManifest(
        batch_id=uuid4(),
        created_at=datetime.now(UTC),
        dataset=dataset.metadata,
        random_seed=42,
        n_components=2,
        csv_filename="embeddings.csv",
        package_versions={},
        runs=[
            BatchRunRecord(
                run_number=1,
                embedding_id=uuid4(),
                config=config,
                observation_count=3,
                execution_time_seconds=1.0,
            )
        ],
    )


@pytest.fixture
def package(tmp_path: Path, manifest: BatchManifest) -> Path:
    source = tmp_path / "export.zip"
    with ZipFile(source, "w", ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest.model_dump_json())
        archive.writestr("embeddings.csv", "index,dim1,dim2\n")
    return source


def test_upload_publishes_manifest_before_zip(package: Path) -> None:
    objects = {}

    def get_blob(key):
        blob = Mock()

        def upload(data, *, overwrite):
            assert overwrite is False
            objects[key] = data if isinstance(data, bytes) else data.read()

        blob.upload_blob.side_effect = upload
        return blob

    container = Mock(spec=ContainerClient)
    container.get_blob_client.side_effect = get_blob

    job_id, token = uuid4(), uuid4()
    artifact_key = BlobJobExports(container).upload(job_id, token, package)
    manifest_key = manifest_blob_key(job_id, artifact_key)

    assert list(objects) == [manifest_key, artifact_key]
    with ZipFile(BytesIO(objects[artifact_key])) as archive:
        assert objects[manifest_key] == archive.read("manifest.json")


@pytest.mark.parametrize("stage", ["manifest", "zip"])
def test_upload_propagates_blob_errors(package: Path, stage: str) -> None:
    job_id, token = uuid4(), uuid4()
    artifact_key = f"jobs/{job_id}/attempts/{token}/export.zip"
    manifest_key = manifest_blob_key(job_id, artifact_key)
    failed_key = manifest_key if stage == "manifest" else artifact_key
    uploaded = []

    def get_blob(key):
        blob = Mock()

        def upload(data, *, overwrite):
            assert overwrite is False
            if key == failed_key:
                raise ResourceExistsError(message="Blob already exists.")
            uploaded.append(key)

        blob.upload_blob.side_effect = upload
        return blob

    container = Mock(spec=ContainerClient)
    container.get_blob_client.side_effect = get_blob

    with pytest.raises(ResourceExistsError):
        BlobJobExports(container).upload(job_id, token, package)

    assert uploaded == ([] if stage == "manifest" else [manifest_key])


def test_invalid_manifest_is_not_uploaded(tmp_path: Path) -> None:
    source = tmp_path / "bad.zip"
    with ZipFile(source, "w") as archive:
        archive.writestr("manifest.json", "{}")

    container = Mock(spec=ContainerClient)

    with pytest.raises(ValueError):
        BlobJobExports(container).upload(uuid4(), uuid4(), source)

    container.get_blob_client.assert_not_called()