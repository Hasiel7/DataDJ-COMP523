from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.models.dataset import PreparedDataset
from app.schemas.dataset import DatasetMetadata


class DatasetStorage(Protocol):
    def save(self, dataset: PreparedDataset) -> None: ...

    def get(self, dataset_id: UUID) -> PreparedDataset | None: ...

    def get_metadata(self, dataset_id: UUID) -> DatasetMetadata | None: ...


def default_dataset_directory() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "datasets"