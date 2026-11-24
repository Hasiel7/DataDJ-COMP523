"""add job lease fields

Revision ID: 569fd8d7a764
Revises: 7c2f4e1a9b3d
Create Date: 2026-11-24 01:43:30.268445

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '569fd8d7a764'
down_revision: Union[str, Sequence[str], None] = '7c2f4e1a9b3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("claim_token", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )

def downgrade() -> None:
    op.drop_column("jobs", "lease_expires_at")
    op.drop_column("jobs", "claim_token")
