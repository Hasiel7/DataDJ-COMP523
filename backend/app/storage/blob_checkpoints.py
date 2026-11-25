from uuid import UUID

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import ContainerClient

from app.models.dataset import PreparedDataset
from app.schemas.embedding import EmbeddingRequest, EmbeddingResult
from app.storage.checkpoints import (
    checkpoint_blob_key,
    checkpoint_identity_hash,
    decode_checkpoint,
    encode_checkpoint,
)


class BlobRunCheckpoints:
    def __init__(self, container: ContainerClient, job_id: UUID, token: UUID, identity: str) -> None:
        self.container = container
        self.job_id = job_id
        self.token = token
        self.identity_hash = checkpoint_identity_hash(identity)

    def _key(self, run_number: int) -> str:
        return checkpoint_blob_key(self.job_id, self.token, self.identity_hash, run_number)

    def load(
        self, run_number: int, dataset: PreparedDataset, request: EmbeddingRequest
    ) -> EmbeddingResult | None:
        return self.load_key(self._key(run_number), run_number, dataset, request)
    
    def load_key(
        self,
        blob_key: str,
        run_number: int,
        dataset: PreparedDataset,
        request: EmbeddingRequest,
    ) -> EmbeddingResult | None:
        parts = blob_key.split("/")
        if len(parts) != 7:
            raise ValueError("Invalid published checkpoint key.")

        try:
            attempt_token = UUID(parts[3])
        except ValueError as error:
            raise ValueError("Invalid published checkpoint key.") from error

        expected = checkpoint_blob_key(
            self.job_id, attempt_token, self.identity_hash, run_number
        )
        if blob_key != expected:
            raise ValueError("Invalid published checkpoint key.")

        blob = self.container.get_blob_client(blob_key)
        try:
            data = blob.download_blob().readall()
        except ResourceNotFoundError as error:
            if getattr(error, "error_code", None) == "BlobNotFound":
                return None
            raise

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("Invalid blob checkpoint encoding.") from error

        result = decode_checkpoint(text, dataset, request)
        if result is None:
            raise ValueError("Invalid or mismatched blob checkpoint.")
        return result
    
    def save(self, run_number: int, result: EmbeddingResult) -> None:
        blob = self.container.get_blob_client(self._key(run_number))
        blob.upload_blob(encode_checkpoint(result).encode("utf-8"), overwrite=False)