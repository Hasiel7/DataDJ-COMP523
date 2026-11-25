from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.user import User  # noqa: F401


class ResourceOwnership(Base):
    __tablename__ = "resource_ownership"
    __table_args__ = (
        CheckConstraint(
            "resource_type IN ('dataset', 'job', 'batch')",
            name="ck_resource_ownership_type",
        ),
    )

    resource_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    resource_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
