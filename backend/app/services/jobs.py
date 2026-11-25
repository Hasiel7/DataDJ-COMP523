from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import NamedTuple
from uuid import UUID, uuid4

from sqlalchemy import Interval, and_, func, literal, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from app.models.job import Job, JobCheckpoint, JobStatus
from app.models.ownership import ResourceOwnership
from app.schemas.execution_plan import ExecutionPlan
from app.schemas.grid import GridPreviewRequest
from app.schemas.job import JobSummary
from app.services.embeddings import DatasetNotFoundError
from app.services.grids import expand_job_grid
from app.storage.base import DatasetStorage
from app.storage.checkpoints import (
    checkpoint_blob_key,
    checkpoint_identity_hash,
    validate_checkpoint_run_number,
)


class JobNotFoundError(LookupError):
    """Raised when an internal operation references an unknown job."""


class ExecutionPlanUnavailableError(ValueError):
    """Raised when a legacy job has no saved execution plan."""

class LeaseLostError(RuntimeError):
    """Raised when a worker no longer owns a valid job lease."""

def leasable_job_condition() -> ColumnElement[bool]:
    return and_(
        Job.execution_plan.is_not(None),
        or_(
            Job.status == JobStatus.QUEUED,
            and_(
                Job.status == JobStatus.RUNNING,
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at <= func.clock_timestamp(),
            ),
        ),
    )

class PublishedCheckpoint(NamedTuple):
    blob_key: str
    embedding_id: UUID

class JobService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        dataset_storage: DatasetStorage,
    ) -> None:
        self.session_factory = session_factory
        self.dataset_storage = dataset_storage

    def worker_lock_path(self) -> Path:
        with self.session_factory() as session:
            database = session.get_bind().engine.url.database
        if not database or database == ":memory:":
            raise ValueError("Workers require a file-backed SQLite database.")
        path = Path(database).resolve()
        return path.with_suffix(path.suffix + ".worker.lock")

    def next_pending(self) -> UUID | None:
        with self.session_factory.begin() as session:
            return session.scalar(
                select(Job.id)
                .where(
                    Job.status.in_((JobStatus.RUNNING, JobStatus.QUEUED)),
                    Job.execution_plan.is_not(None),
                )
                .order_by((Job.status == JobStatus.RUNNING).desc(), Job.created_at, Job.id)
                .limit(1)
            )
    def validate_artifact_path(self, artifact_path: str) -> str:
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
        return path.as_posix()
    
    def validate_error_message(self, error_message: str) -> str:
        message = error_message.strip()
        if not message or len(message) > 2000:
            raise ValueError("Error message must contain 1 through 2000 characters.")
        return message
        
    def submit(self, request: GridPreviewRequest, *, owner_id: UUID | None = None) -> UUID:
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
            if owner_id is not None:
                session.add(ResourceOwnership(
                    resource_type="job", resource_id=job_id, user_id=owner_id
                ))

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
        
    def claim(self, job_id: UUID, *, resume: bool = False) -> ExecutionPlan | None:
        with self.session_factory.begin() as session:
            statement = (
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.status.in_(
                        (JobStatus.QUEUED, JobStatus.RUNNING) if resume else (JobStatus.QUEUED,)
                    ),
                    Job.execution_plan.is_not(None),
                    Job.claim_token.is_(None),
                )
                .values(
                    status=JobStatus.RUNNING,
                    started_at=func.coalesce(Job.started_at, datetime.now(UTC)),
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
        artifact_key = self.validate_artifact_path(artifact_path)

        with self.session_factory.begin() as session:
            statement = (
                update(Job)
                .where(Job.id == job_id, Job.status == JobStatus.RUNNING, Job.claim_token.is_(None))
                .values(
                    status=JobStatus.SUCCEEDED,
                    finished_at=datetime.now(UTC),
                    artifact_path=artifact_key,
                    error_message=None,
                )
                .returning(Job.id)
            )
            updated_id = session.scalars(statement).one_or_none()
        return updated_id is not None

    def fail(self, job_id: UUID, error_message: str) -> bool:
        message = self.validate_error_message(error_message)
        if not message or len(message) > 2000:
            raise ValueError("Error message must contain 1 through 2000 characters.")

        with self.session_factory.begin() as session:
            statement = (
                update(Job)
                .where(Job.id == job_id, Job.status == JobStatus.RUNNING, Job.claim_token.is_(None))
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

    def claim_with_lease(self, job_id: UUID
        ) -> tuple[UUID, ExecutionPlan] | None:
        token = uuid4()

        with self.session_factory.begin() as session:
            if session.get_bind().dialect.name != "postgresql":
                raise RuntimeError("Claim with lease is only supported on PostgreSQL.")

            now = func.clock_timestamp()
            lease_duration = literal(timedelta(minutes=5), type_=Interval())

            statement = (
                update(Job)
                .where(
                    Job.id == job_id,
                    leasable_job_condition(),
                )
                .values(
                    status = JobStatus.RUNNING,
                    started_at=func.coalesce(Job.started_at, now),
                    claim_token = token,
                    lease_expires_at = now + lease_duration
                )
                .returning(Job)
            )

            job = session.scalars(statement).one_or_none()
            if job is None:
                return None

            plan = ExecutionPlan.model_validate(job.execution_plan)
            if plan.dataset_id != job.dataset_id:
                raise ValueError("Execution plan dataset ID does not match job dataset ID.")
        return token, plan

    def renew_lease(self, job_id: UUID, token: UUID) -> bool:
        with self.session_factory.begin() as session:
            if session.get_bind().dialect.name != "postgresql":
                raise RuntimeError("Lease renewal is only supported on PostgreSQL.")
            
            session.execute(text("SET LOCAL statement_timeout = '10s'"))
            session.execute(text("SET LOCAL lock_timeout = '5s'"))

            locked_id = session.scalar(
                select(Job.id).where(Job.id == job_id).with_for_update()
            )
            if locked_id is None:
                return False
            
            now = func.clock_timestamp()
            lease_duration = literal(timedelta(minutes=5), type_=Interval())

            statement = (
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.status == JobStatus.RUNNING,
                    Job.claim_token == token,
                    Job.lease_expires_at > now,
                )
                .values(lease_expires_at=now + lease_duration)
                .returning(Job.id)
            )

            return session.scalar(statement) is not None

    def _finish_leased(self, job_id: UUID, token: UUID, *, status: JobStatus, artifact_path: str | None, error_message: str | None) -> bool:
        with self.session_factory.begin() as session:
            if session.get_bind().dialect.name != "postgresql":
                raise RuntimeError("Lease renewal is only supported on PostgreSQL.")
            
            locked_id = session.scalar(
                select(Job.id).where(Job.id == job_id).with_for_update()
            )
            if locked_id is None:
                return False
            
            now = func.clock_timestamp()
            statement = (
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.status == JobStatus.RUNNING,
                    Job.claim_token == token,
                    Job.lease_expires_at > now,
                )
                .values(
                    status=status,
                    finished_at=now,
                    artifact_path=artifact_path,
                    error_message=error_message,
                    claim_token=None,
                    lease_expires_at=None
                )
                .returning(Job.id)
            )
            return session.scalar(statement) is not None

    def succeed_with_lease(
        self, job_id: UUID, token: UUID, artifact_path: str
    ) -> bool:
        return self._finish_leased(
            job_id,
            token,
            status=JobStatus.SUCCEEDED,
            artifact_path=self.validate_artifact_path(artifact_path),
            error_message=None,
        )

    def fail_with_lease(
        self, job_id: UUID, token: UUID, error_message: str
    ) -> bool:
        return self._finish_leased(
            job_id,
            token,
            status=JobStatus.FAILED,
            artifact_path=None,
            error_message=self.validate_error_message(error_message),
        )
    def next_leasable(self, *, owned_only: bool = False) -> UUID | None:
        with self.session_factory.begin() as session:
            if session.get_bind().dialect.name != "postgresql":
                raise RuntimeError("Lease renewal is only supported on PostgreSQL.")

            statement = (
                select(Job.id)
                .where(leasable_job_condition())
                .order_by(
                    (Job.status == JobStatus.RUNNING).desc(),
                    Job.created_at,
                    Job.id
                )
                .limit(1)
            )
            if owned_only:
                statement = statement.where(
                    select(ResourceOwnership.resource_id)
                    .where(
                        ResourceOwnership.resource_type == "job",
                        ResourceOwnership.resource_id == Job.id,
                    )
                    .exists()
                )
            return session.scalar(statement)
    def publish_checkpoint(
        self,
        job_id: UUID,
        token: UUID,
        identity: str,
        run_number: int,
        embedding_id: UUID,
    ) -> PublishedCheckpoint | None:
        identity_hash = checkpoint_identity_hash(identity)
        blob_key = checkpoint_blob_key(job_id, token, identity_hash, run_number)

        with self.session_factory.begin() as session:
            if session.get_bind().dialect.name != "postgresql":
                raise RuntimeError("Checkpoint publication requires PostgreSQL.")

            locked_id = session.scalar(
                select(Job.id).where(Job.id == job_id).with_for_update()
            )
            if locked_id is None:
                return None

            owned_id = session.scalar(
                select(Job.id).where(
                    Job.id == job_id,
                    Job.status == JobStatus.RUNNING,
                    Job.claim_token == token,
                    Job.lease_expires_at > func.clock_timestamp(),
                )
            )
            if owned_id is None:
                return None

            statement = (
                insert(JobCheckpoint)
                .values(
                    job_id=job_id,
                    identity_hash=identity_hash,
                    run_number=run_number,
                    blob_key=blob_key,
                    embedding_id=embedding_id,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        JobCheckpoint.job_id,
                        JobCheckpoint.identity_hash,
                        JobCheckpoint.run_number,
                    ]
                )
            )
            session.execute(statement)

            row = session.execute(
                select(JobCheckpoint.blob_key, JobCheckpoint.embedding_id).where(
                    JobCheckpoint.job_id == job_id,
                    JobCheckpoint.identity_hash == identity_hash,
                    JobCheckpoint.run_number == run_number,
                )
            ).one()
            return PublishedCheckpoint(row.blob_key, row.embedding_id)

    def get_published_checkpoint(
        self, job_id: UUID, token: UUID, identity: str, run_number: int
    ) -> PublishedCheckpoint | None:
        validate_checkpoint_run_number(run_number)
        identity_hash = checkpoint_identity_hash(identity)

        with self.session_factory.begin() as session:
            if session.get_bind().dialect.name != "postgresql":
                raise RuntimeError("Checkpoint lookup requires PostgreSQL.")

            statement = (
                select(JobCheckpoint.blob_key, JobCheckpoint.embedding_id)
                .select_from(Job)
                .outerjoin(
                    JobCheckpoint,
                    and_(
                        JobCheckpoint.job_id == Job.id,
                        JobCheckpoint.identity_hash == identity_hash,
                        JobCheckpoint.run_number == run_number,
                    ),
                )
                .where(
                    Job.id == job_id,
                    Job.status == JobStatus.RUNNING,
                    Job.claim_token == token,
                    Job.lease_expires_at > func.clock_timestamp(),
                )
            )
            row = session.execute(statement).one_or_none()

            if row is None:
                raise LeaseLostError("Job lease is no longer valid.")
            if row.blob_key is None:
                return None
            if row.embedding_id is None:
                raise RuntimeError("Published checkpoint has no embedding ID.")

            return PublishedCheckpoint(row.blob_key, row.embedding_id)

    def get_completed_checkpoint(
        self, job_id: UUID, run_number: int, embedding_id: UUID
    ) -> PublishedCheckpoint | None:
        validate_checkpoint_run_number(run_number)

        with self.session_factory.begin() as session:
            rows = session.execute(
                select(
                    JobCheckpoint.identity_hash,
                    JobCheckpoint.blob_key,
                    JobCheckpoint.embedding_id,
                )
                .join(Job, Job.id == JobCheckpoint.job_id)
                .where(
                    Job.id == job_id,
                    Job.status == JobStatus.SUCCEEDED,
                    Job.artifact_path.is_not(None),
                    JobCheckpoint.run_number == run_number,
                    JobCheckpoint.embedding_id == embedding_id,
                )
                .limit(2)
            ).all()

            if not rows:
                return None

            if len(rows) != 1:
                raise RuntimeError(
                    "Multiple published checkpoints match the completed run."
                )

            row = rows[0]
            parts = row.blob_key.split("/")
            if len(parts) != 7:
                raise ValueError("Invalid completed checkpoint key.")

            try:
                token = UUID(parts[3])
                expected = checkpoint_blob_key(
                    job_id, token, row.identity_hash, run_number
                )
            except ValueError as error:
                raise ValueError("Invalid completed checkpoint key.") from error

            if row.blob_key != expected:
                raise ValueError("Invalid completed checkpoint key.")

            return PublishedCheckpoint(row.blob_key, row.embedding_id)