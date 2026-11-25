from uuid import UUID

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import ContainerClient

from app.models.job import JobStatus
from app.schemas.batch import BatchManifest
from app.schemas.embedding import EmbeddingResult
from app.services.jobs import JobService
from app.storage.blob_job_exports import manifest_blob_key
from app.storage.checkpoints import (
    decode_checkpoint_payload,
    validate_checkpoint_run_number,
)


class CompletedBlobResults:
    def __init__(self, jobs: JobService, container: ContainerClient) -> None:
        self.jobs = jobs
        self.container = container

    def load_manifest(self, job_id: UUID) -> BatchManifest:
        summary = self.jobs.get(job_id)
        if (
            summary is None
            or summary.status != JobStatus.SUCCEEDED
            or summary.artifact_path is None
        ):
            raise LookupError("Completed job not found.")

        key = manifest_blob_key(job_id, summary.artifact_path)
        blob = self.container.get_blob_client(key)

        try:
            data = blob.download_blob().readall()
        except ResourceNotFoundError as error:
            if getattr(error, "error_code", None) == "BlobNotFound":
                raise FileNotFoundError(
                    "Published manifest blob is missing."
                ) from error
            raise

        manifest = BatchManifest.model_validate_json(data)
        if manifest.dataset.dataset_id != summary.dataset_id:
            raise ValueError("Manifest dataset does not match the job.")

        return manifest
    
    def load_run(
        self, job_id: UUID, manifest: BatchManifest, run_number: int
    ) -> EmbeddingResult:
        validate_checkpoint_run_number(run_number)

        summary = self.jobs.get(job_id)
        if (
            summary is None
            or summary.status != JobStatus.SUCCEEDED
            or summary.artifact_path is None
        ):
            raise LookupError("Completed job not found.")

        if summary.dataset_id != manifest.dataset.dataset_id:
            raise ValueError("Manifest dataset does not match the job.")

        records = [run for run in manifest.runs if run.run_number == run_number]
        if not records:
            raise LookupError("Run not found in manifest.")
        if len(records) != 1:
            raise ValueError("Manifest contains duplicate run numbers.")
        record = records[0]

        published = self.jobs.get_completed_checkpoint(
            job_id, run_number, record.embedding_id
        )
        if published is None:
            raise FileNotFoundError("Published checkpoint reference is missing.")

        blob = self.container.get_blob_client(published.blob_key)
        try:
            data = blob.download_blob().readall()
        except ResourceNotFoundError as error:
            if getattr(error, "error_code", None) == "BlobNotFound":
                raise FileNotFoundError(
                    "Published checkpoint blob is missing."
                ) from error
            raise

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("Invalid checkpoint encoding.") from error

        result = decode_checkpoint_payload(text)
        if result is None:
            raise ValueError("Invalid checkpoint payload.")

        if (
            result.embedding_id != published.embedding_id
            or result.embedding_id != record.embedding_id
            or result.dataset_id != manifest.dataset.dataset_id
            or result.algorithm != record.config.algorithm
            or result.hyperparameters
            != record.config.model_dump(exclude={"algorithm"})
            or result.random_seed != manifest.random_seed
            or result.observation_count != record.observation_count
            or result.observation_count != manifest.dataset.observation_count
            or result.output_dimensions != record.config.n_components
            or result.output_dimensions != manifest.n_components
        ):
            raise ValueError("Checkpoint does not match the completed manifest.")

        return result