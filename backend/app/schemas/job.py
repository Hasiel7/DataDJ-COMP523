from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.job import JobStatus


class JobSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    dataset_id: UUID
    status: JobStatus
    artifact_path: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @field_validator("created_at", "started_at", "finished_at", mode="after")
    @classmethod
    def normalize_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

class JobSubmissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    status: Literal["queued"] = "queued"