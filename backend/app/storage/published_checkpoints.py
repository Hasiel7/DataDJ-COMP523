from uuid import UUID

from azure.storage.blob import ContainerClient

from app.models.dataset import PreparedDataset
from app.schemas.embedding import EmbeddingRequest, EmbeddingResult
from app.services.jobs import JobService, LeaseLostError
from app.storage.blob_checkpoints import BlobRunCheckpoints


class PublishedBlobCheckpoints:
    def __init__(
        self,
        container: ContainerClient,
        jobs: JobService,
        job_id: UUID,
        token: UUID,
        identity: str,
    ) -> None:
        self.jobs = jobs
        self.job_id = job_id
        self.token = token
        self.identity = identity
        self.blobs = BlobRunCheckpoints(container, job_id, token, identity)

    def load(
        self, run_number: int, dataset: PreparedDataset, request: EmbeddingRequest
    ) -> EmbeddingResult | None:
        published = self.jobs.get_published_checkpoint(
            self.job_id, self.token, self.identity, run_number
        )
        if published is None:
            return None

        result = self.blobs.load_key(published.blob_key, run_number, dataset, request)
        if result is None:
            raise FileNotFoundError("Published checkpoint blob is missing.")
        if result.embedding_id != published.embedding_id:
            raise ValueError("Published checkpoint embedding ID does not match.")
        return result

    def save(self, run_number: int, result: EmbeddingResult) -> None:
        existing = self.jobs.get_published_checkpoint(
            self.job_id, self.token, self.identity, run_number
        )
        if existing is not None:
            return

        self.blobs.save(run_number, result)
        published = self.jobs.publish_checkpoint(
            self.job_id, self.token, self.identity, run_number, result.embedding_id
        )
        if published is None:
            raise LeaseLostError("Job lease was lost before checkpoint publication.")