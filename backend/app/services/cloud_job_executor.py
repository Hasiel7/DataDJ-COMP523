import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from azure.storage.blob import ContainerClient

from app.services.batches import BatchService
from app.services.jobs import JobService, LeaseLostError
from app.services.lease_heartbeat import LeaseHeartbeat
from app.storage.blob_job_exports import BlobJobExports
from app.storage.published_checkpoints import PublishedBlobCheckpoints

logger = logging.getLogger(__name__)


class CloudJobExecutor:
    def __init__(
        self,
        jobs: JobService,
        batches: BatchService,
        container: ContainerClient,
    ) -> None:
        self.jobs = jobs
        self.batches = batches
        self.container = container
        self.exports = BlobJobExports(container)

    def execute(self, job_id: UUID) -> bool:
        claimed = self.jobs.claim_with_lease(job_id)
        if claimed is None:
            return False

        token, plan = claimed
        heartbeat = LeaseHeartbeat(self.jobs, job_id, token)
        heartbeat.start()

        try:
            try:
                with TemporaryDirectory(prefix="data-dj-cloud-job-") as directory:
                    heartbeat.raise_if_lost()

                    def checkpoint_factory(identity: str) -> PublishedBlobCheckpoints:
                        return PublishedBlobCheckpoints(
                            self.container,
                            self.jobs,
                            job_id,
                            token,
                            identity,
                        )

                    package = self.batches.compute_plan_package(
                        plan,
                        Path(directory),
                        checkpoint_factory=checkpoint_factory,
                    )

                    if not package.is_file():
                        raise FileNotFoundError("Batch package was not published.")

                    heartbeat.raise_if_lost()
                    artifact_key = self.exports.upload(job_id, token, package)
                    heartbeat.raise_if_lost()
            finally:
                heartbeat.stop()

            heartbeat.raise_if_lost()

        except LeaseLostError:
            raise

        except Exception as error:
            logger.exception("Cloud job %s failed", job_id)

            # A failed heartbeat shutdown makes ownership uncertain.
            heartbeat.raise_if_lost()

            recorded = self.jobs.fail_with_lease(
                job_id,
                token,
                "Batch execution failed; consult worker logs.",
            )
            if not recorded:
                raise LeaseLostError(
                    "Job lease was lost before failure could be recorded."
                ) from error

            raise

        if not self.jobs.succeed_with_lease(job_id, token, artifact_key):
            raise LeaseLostError(
                "Job lease was lost before success could be recorded."
            )

        return True