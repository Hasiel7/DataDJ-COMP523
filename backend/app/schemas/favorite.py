from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FavoriteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    file_name: str = Field(min_length=1, max_length=512)
    name: str = Field(min_length=1, max_length=512)
    # The visualizer's run + view state, stored as given
    payload: dict[str, Any]


class FavoritePublic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    file_name: str
    name: str
    payload: dict[str, Any]
    created_at: datetime

    @field_validator("created_at", mode="after")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
