from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO
from uuid import UUID

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import ContainerClient

from app.schemas.batch import BatchCatalog, BatchRunData
from app.storage.batches import LocalBatchStorage


class BlobBatchStorage:
    def __init__(self, container: ContainerClient) -> None:
        self.container = container

    def import_csv(self, source: BinaryIO, filename: str) -> BatchCatalog:
        with TemporaryDirectory(prefix="data-dj-import-") as directory:
            root = Path(directory)
            catalog = LocalBatchStorage(root).import_csv(source, filename)
            batch_dir = root / str(catalog.batch_id)
            prefix = f"batches/{catalog.batch_id}"
            for run in catalog.runs:
                path = batch_dir / "runs" / f"{run.run_number}.csv.gz"
                with path.open("rb") as stream:
                    self.container.get_blob_client(
                        f"{prefix}/runs/{run.run_number}.csv.gz"
                    ).upload_blob(stream, overwrite=False)
            with (batch_dir / "catalog.json").open("rb") as stream:
                self.container.get_blob_client(
                    f"{prefix}/catalog.json"
                ).upload_blob(stream, overwrite=False)
            return catalog

    def get_catalog(self, batch_id: UUID) -> BatchCatalog | None:
        blob = self.container.get_blob_client(f"batches/{batch_id}/catalog.json")
        try:
            data = blob.download_blob().readall()
        except ResourceNotFoundError as error:
            if getattr(error, "error_code", None) == "BlobNotFound":
                return None
            raise
        catalog = BatchCatalog.model_validate_json(data)
        if catalog.batch_id != batch_id:
            raise ValueError("Batch catalog ID does not match its key.")
        return catalog

    def get_run(self, batch_id: UUID, run_number: int) -> BatchRunData | None:
        catalog = self.get_catalog(batch_id)
        if catalog is None:
            return None
        if not any(run.run_number == run_number for run in catalog.runs):
            return None

        blob = self.container.get_blob_client(f"batches/{batch_id}/runs/{run_number}.csv.gz")
        with TemporaryDirectory(prefix="data-dj-batch-read-") as directory:
            root = Path(directory)
            batch_dir = root / str(batch_id)
            runs_dir = batch_dir / "runs"
            runs_dir.mkdir(parents=True)
            (batch_dir / "catalog.json").write_text(catalog.model_dump_json(), encoding="utf-8")
            try:
                with (runs_dir / f"{run_number}.csv.gz").open("wb") as stream:
                    blob.download_blob().readinto(stream)
            except ResourceNotFoundError as error:
                if getattr(error, "error_code", None) == "BlobNotFound":
                    raise FileNotFoundError("Published batch run is missing.") from error
                raise
            result = LocalBatchStorage(root).get_run(batch_id, run_number)
            if result is None:
                raise ValueError("Published batch run is invalid.")
            return result
