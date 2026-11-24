import json
from tempfile import TemporaryFile
from typing import cast
from uuid import UUID

import numpy as np
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import ContainerClient

from app.models.dataset import PreparedDataset
from app.schemas.dataset import DatasetMetadata, JsonScalar


class BlobDatasetStorage:
    def __init__(self, container: ContainerClient) -> None:
        self.container = container

    @staticmethod
    def _key(dataset_id: UUID, filename: str) -> str:
        return f"datasets/{dataset_id}/{filename}"

    def save(self, dataset: PreparedDataset) -> None:
        dataset_id = dataset.metadata.dataset_id
        features_blob = self.container.get_blob_client(self._key(dataset_id, "features.npy"))

        metadata_blob = self.container.get_blob_client(self._key(dataset_id, "metadata.json"))

        with TemporaryFile() as output:
            np.save(output, dataset.features, allow_pickle=False)
            output.seek(0)
            features_blob.upload_blob(output, overwrite=False)

        payload = {
            "metadata": dataset.metadata.model_dump(mode="json"),
            "row_ids": dataset.row_ids,
            "labels": dataset.labels,
        }
        metadata_blob.upload_blob(json.dumps(payload, allow_nan=False).encode("utf-8"), overwrite=False)

    def _get_payload(self, dataset_id: UUID) -> dict:
        blob = self.container.get_blob_client(self._key(dataset_id, "metadata.json"))
        return json.loads(blob.download_blob().readall())

    def get_metadata(self, dataset_id: UUID) -> DatasetMetadata | None:
        try:
            payload = self._get_payload(dataset_id)
        except ResourceNotFoundError as error:
            if getattr(error, "error_code", None) == "BlobNotFound":
                return None
            raise

        return DatasetMetadata.model_validate(payload["metadata"])

    def get(self, dataset_id: UUID) -> PreparedDataset | None:
        try:
            payload = self._get_payload(dataset_id)
        except ResourceNotFoundError as error:
            if getattr(error, "error_code", None) == "BlobNotFound":
                return None
            raise

        blob =self.container.get_blob_client(self._key(dataset_id, "features.npy"))
        with TemporaryFile() as output:
            blob.download_blob().readinto(output)
            output.seek(0)
            features = np.load(output, allow_pickle=False)

        metadata = DatasetMetadata.model_validate(payload["metadata"])
        row_ids = [cast(JsonScalar, value) for value in payload["row_ids"]]
        labels_data = payload["labels"]
        labels = (
            [cast(JsonScalar, value) for value in labels_data]
            if labels_data is not None
            else None
        )

        return PreparedDataset(
            metadata=metadata,
            features=features,
            row_ids=row_ids,
            labels=labels,
        )
