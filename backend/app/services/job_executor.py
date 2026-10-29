import logging
from pathlib import Path
from uuid import UUID

from app.services.batches import BatchService
from app.services.jobs import JobService

logger = logging.getLogger(__name__)


class JobExecutor:
    def __init__(
        self,
        jobs: JobService,
        batches: BatchService,
        artifact_root: Path,
    ) -> None:
        self.jobs = jobs
        self.batches = batches
        self.artifact_root = artifact_root.resolve()

    def execute(self, job_id: UUID) -> bool:
        plan = self.jobs.claim(job_id)
        if plan is None:
            return False

        try:
            package = self.batches.compute_plan_package(
                plan, self.artifact_root / "exports"
            )
            package = package.resolve()
            artifact_key = package.relative_to(self.artifact_root).as_posix()
            if not package.is_file():
                raise FileNotFoundError("Batch package was not published.")
        except Exception:
            logger.exception("Batch execution failed for job %s", job_id)
            try:
                recorded = self.jobs.fail(
                    job_id, "Batch execution failed; consult server logs."
                )
            except Exception:
                logger.exception("Could not record failure for job %s", job_id)
            else:
                if not recorded:
                    logger.error("Could not record failure for job %s", job_id)
            raise

        if not self.jobs.succeed(job_id, artifact_key):
            raise RuntimeError("Job no longer running; completion was not recorded.")
        return True