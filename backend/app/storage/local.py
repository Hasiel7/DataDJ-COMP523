import json
from pathlib import Path
from typing import cast
from uuid import UUID

import numpy as np

from app.models.dataset import PreparedDataset
from app.schemas.dataset import DatasetMetadata, JsonScalar
from app.storage.base import default_dataset_directory


class LocalDatasetStorage:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else default_dataset_directory()

    def save(self, dataset: PreparedDataset) -> None:
        dataset_dir = self.root / str(dataset.metadata.dataset_id)
        dataset_dir.mkdir(parents=True, exist_ok=False)

        temporary_features_path = dataset_dir / "features.npy.tmp"
        with temporary_features_path.open("wb") as features_file:
            np.save(features_file, dataset.features, allow_pickle=False)
        temporary_features_path.replace(dataset_dir / "features.npy")

        payload = {
            "metadata": dataset.metadata.model_dump(mode="json"),
            "row_ids": dataset.row_ids,
            "labels": dataset.labels,
        }
        temporary_metadata_path = dataset_dir / "metadata.json.tmp"
        temporary_metadata_path.write_text(
            json.dumps(payload, allow_nan=False), encoding="utf-8"
        )
        temporary_metadata_path.replace(dataset_dir / "metadata.json")

    def get(self, dataset_id: UUID) -> PreparedDataset | None:
        dataset_dir = self.root / str(dataset_id)
        metadata_path = dataset_dir / "metadata.json"
        features_path = dataset_dir / "features.npy"
        if not metadata_path.is_file() or not features_path.is_file():
            return None

        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata = DatasetMetadata.model_validate(payload["metadata"])
        features = np.load(features_path, allow_pickle=False)
        row_ids = [cast(JsonScalar, value) for value in payload["row_ids"]]
        labels_data = payload["labels"]
        labels = (
            [cast(JsonScalar, value) for value in labels_data]
            if labels_data is not None
            else None
        )
        return PreparedDataset(metadata, features, row_ids, labels)

    def get_metadata(self, dataset_id: UUID) -> DatasetMetadata | None:
        metadata_path = self.root / str(dataset_id) / "metadata.json"
        if not metadata_path.is_file():
            return None
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        return DatasetMetadata.model_validate(payload["metadata"])