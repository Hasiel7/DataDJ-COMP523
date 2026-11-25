from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    dataset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, index=True
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(
            JobStatus,
            values_callable=lambda statuses: [status.value for status in statuses],
            native_enum=False,
            create_constraint=True,
            name="job_status",
        ),
        nullable=False,
        default=JobStatus.QUEUED,
        index=True,
    )
    request_payload: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False
    )
    execution_plan: Mapped[dict[str, object] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claim_token: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    artifact_path: Mapped[str | None] = mapped_column(
        String(1024), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

class JobCheckpoint(Base):
    __tablename__ = "job_checkpoints"
    __table_args__ = (
        CheckConstraint("run_number > 0", name="ck_job_checkpoints_run_positive"),
        Index(
            "ix_job_checkpoints_job_run_embedding",
            "job_id",
            "run_number",
            "embedding_id",
        ),
    )

    job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    identity_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    blob_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    embedding_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)