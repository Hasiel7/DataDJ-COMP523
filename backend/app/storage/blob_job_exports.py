from pathlib import Path
from uuid import UUID
from zipfile import ZipFile

from azure.storage.blob import ContainerClient

from app.schemas.batch import BatchManifest


def manifest_blob_key(job_id: UUID, artifact_key: str) -> str:
    parts = artifact_key.split("/")
    if len(parts) != 5:
        raise ValueError("Invalid cloud export key.")

    try:
        token = UUID(parts[3])
    except ValueError as error:
        raise ValueError("Invalid cloud export key.") from error

    expected = f"jobs/{job_id}/attempts/{token}/export.zip"
    if artifact_key != expected:
        raise ValueError("Invalid cloud export key.")

    return f"jobs/{job_id}/attempts/{token}/manifest.json"


class BlobJobExports:
    def __init__(self, container: ContainerClient) -> None:
        self.container = container

    def upload(self, job_id: UUID, token: UUID, source: Path) -> str:
        key = f"jobs/{job_id}/attempts/{token}/export.zip"
        manifest_key = manifest_blob_key(job_id, key)

        with ZipFile(source) as archive:
            entries = [
                item for item in archive.infolist()
                if item.filename == "manifest.json"
            ]
            if len(entries) != 1:
                raise ValueError("Export must contain exactly one manifest.")
            manifest_data = archive.read(entries[0])

        BatchManifest.model_validate_json(manifest_data)

        self.container.get_blob_client(manifest_key).upload_blob(
            manifest_data, overwrite=False
        )
        with source.open("rb") as stream:
            self.container.get_blob_client(key).upload_blob(
                stream, overwrite=False
            )

        return key