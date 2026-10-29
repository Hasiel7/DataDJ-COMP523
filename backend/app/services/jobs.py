from datetime import UTC, datetime
from pathlib import PurePosixPath
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from app.models.job import Job, JobStatus
from app.schemas.execution_plan import ExecutionPlan
from app.schemas.grid import GridPreviewRequest
from app.schemas.job import JobSummary
from app.services.embeddings import DatasetNotFoundError
from app.services.grids import expand_job_grid
from app.storage.base import DatasetStorage


class JobNotFoundError(LookupError):
    """Raised when an internal operation references an unknown job."""


class ExecutionPlanUnavailableError(ValueError):
    """Raised when a legacy job has no saved execution plan."""

class JobService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        dataset_storage: DatasetStorage,
    ) -> None:
        self.session_factory = session_factory
        self.dataset_storage = dataset_storage

    def submit(self, request: GridPreviewRequest) -> UUID:
        metadata = self.dataset_storage.get_metadata(request.dataset_id)
        if metadata is None:
            raise DatasetNotFoundError("Dataset not found.")

        configs = expand_job_grid(
            request,
            n_samples=metadata.observation_count,
        )
        plan = ExecutionPlan(
            dataset_id=request.dataset_id,
            observation_count=metadata.observation_count,
            n_components=request.n_components,
            random_seed=request.random_seed,
            configs=tuple(configs),
        )
        with self.session_factory.begin() as session:
            job = Job(
                dataset_id=request.dataset_id,
                request_payload=request.model_dump(mode="json"),
                execution_plan=plan.model_dump(mode="json"),
            )
            session.add(job)
            session.flush()
            job_id = job.id

        return job_id
    def get(self, job_id: UUID) -> JobSummary | None:
        with self.session_factory.begin() as session:
            job = session.get(Job, job_id)
            if job is None:
                return None
            return JobSummary(
                job_id=job.id,
                dataset_id=job.dataset_id,
                status=job.status,
                artifact_path=job.artifact_path,
                created_at=job.created_at,
                started_at=job.started_at,
                finished_at=job.finished_at,
            )
    def get_execution_plan(self, job_id: UUID) -> ExecutionPlan:
        with self.session_factory.begin() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise JobNotFoundError("Job not found.")
            if job.execution_plan is None:
                raise ExecutionPlanUnavailableError(
                    "This job has no saved execution plan; submit a new job."
                )

            plan = ExecutionPlan.model_validate(job.execution_plan)
            if plan.dataset_id != job.dataset_id:
                raise ValueError("Execution plan dataset does not match the job.")
            return plan
        
    def claim(self, job_id: UUID) -> ExecutionPlan | None:
        with self.session_factory.begin() as session:
            statement = (
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.status == JobStatus.QUEUED,
                    Job.execution_plan.is_not(None),
                )
                .values(
                    status=JobStatus.RUNNING,
                    started_at=datetime.now(UTC),
                )
                .returning(Job)
            )
            job = session.scalars(statement).one_or_none()
            if job is None:
                return None

            plan = ExecutionPlan.model_validate(job.execution_plan)
            if plan.dataset_id != job.dataset_id:
                raise ValueError("Execution plan dataset does not match the job.")
            return plan

    def succeed(self, job_id: UUID, artifact_path: str) -> bool:
        path = PurePosixPath(artifact_path)
        if (
            not artifact_path
            or "\\" in artifact_path
            or ":" in artifact_path
            or path.is_absolute()
            or ".." in path.parts
            or path.suffix != ".zip"
        ):
            raise ValueError("Artifact must be a relative ZIP path.")

        with self.session_factory.begin() as session:
            statement = (
                update(Job)
                .where(Job.id == job_id, Job.status == JobStatus.RUNNING)
                .values(
                    status=JobStatus.SUCCEEDED,
                    finished_at=datetime.now(UTC),
                    artifact_path=path.as_posix(),
                    error_message=None,
                )
                .returning(Job.id)
            )
            updated_id = session.scalars(statement).one_or_none()
        return updated_id is not None

    def fail(self, job_id: UUID, error_message: str) -> bool:
        message = error_message.strip()
        if not message or len(message) > 2000:
            raise ValueError("Error message must contain 1 through 2000 characters.")

        with self.session_factory.begin() as session:
            statement = (
                update(Job)
                .where(Job.id == job_id, Job.status == JobStatus.RUNNING)
                .values(
                    status=JobStatus.FAILED,
                    finished_at=datetime.now(UTC),
                    artifact_path=None,
                    error_message=message,
                )
                .returning(Job.id)
            )
            updated_id = session.scalars(statement).one_or_none()
        return updated_id is not None