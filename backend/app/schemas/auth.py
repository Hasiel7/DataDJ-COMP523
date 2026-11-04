import re
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Deliberately loose: one @, no spaces, a dot in the domain. Real checking is the
# user being able to sign in with it.
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    email: str = Field(max_length=320)
    # The upper bound keeps a huge body from tying up the password hasher
    password: str = Field(min_length=8, max_length=1024)

    @field_validator("email", mode="after")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        email = value.strip().lower()
        if not _EMAIL_PATTERN.fullmatch(email):
            raise ValueError("Enter a valid email address.")
        return email


class LoginRequest(BaseModel):
    """Login takes any strings, so a malformed email gets the same answer as a wrong one."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    email: str = Field(max_length=320)
    password: str = Field(max_length=1024)


class UserPublic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    email: str
    created_at: datetime

    @field_validator("created_at", mode="after")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class AuthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserPublic
