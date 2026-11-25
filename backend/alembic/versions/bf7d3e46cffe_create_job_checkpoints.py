"""create job checkpoints

Revision ID: bf7d3e46cffe
Revises: 569fd8d7a764
Create Date: 2026-11-24 20:18:17.457725

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bf7d3e46cffe'
down_revision: Union[str, Sequence[str], None] = '569fd8d7a764'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "job_checkpoints",
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("identity_hash", sa.String(64), nullable=False),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column("blob_key", sa.String(1024), nullable=False),
        sa.Column("embedding_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "run_number > 0", name="ck_job_checkpoints_run_positive"
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id", "identity_hash", "run_number"),
    )
    op.create_index(
        "ix_job_checkpoints_job_run_embedding",
        "job_checkpoints",
        ["job_id", "run_number", "embedding_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_job_checkpoints_job_run_embedding", table_name="job_checkpoints"
    )
    op.drop_table("job_checkpoints")
