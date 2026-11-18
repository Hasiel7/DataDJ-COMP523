from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import UUID

from app.models.dataset import PreparedDataset
from app.schemas.batch import BatchCatalog, BatchRunData
from app.schemas.dataset import DatasetMetadata


class DatasetStorage(Protocol):
    def save(self, dataset: PreparedDataset) -> None: ...

    def get(self, dataset_id: UUID) -> PreparedDataset | None: ...

    def get_metadata(self, dataset_id: UUID) -> DatasetMetadata | None: ...


class BatchStorage(Protocol):
    def import_csv(self, source: BinaryIO, filename: str) -> BatchCatalog: ...

    def get_catalog(self, batch_id: UUID) -> BatchCatalog | None: ...

    def get_run(self, batch_id: UUID, run_number: int) -> BatchRunData | None: ...


def default_dataset_directory() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "datasets"
