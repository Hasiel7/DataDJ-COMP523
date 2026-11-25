from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models.ownership import ResourceOwnership

ResourceType = Literal["dataset", "job", "batch"]


class OwnershipService:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def register(self, resource_type: ResourceType, resource_id: UUID, user_id: UUID) -> None:
        with self.sessions.begin() as session:
            session.add(ResourceOwnership(
                resource_type=resource_type, resource_id=resource_id, user_id=user_id
            ))

    def owns(self, resource_type: ResourceType, resource_id: UUID, user_id: UUID) -> bool:
        with self.sessions.begin() as session:
            return session.scalar(
                select(ResourceOwnership.resource_id).where(
                    ResourceOwnership.resource_type == resource_type,
                    ResourceOwnership.resource_id == resource_id,
                    ResourceOwnership.user_id == user_id,
                )
            ) is not None
