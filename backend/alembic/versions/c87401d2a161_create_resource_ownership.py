"""Record owners for newly created datasets, jobs and imported batches."""

import sqlalchemy as sa

from alembic import op

revision = "c87401d2a161"
down_revision = "bf7d3e46cffe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resource_ownership",
        sa.Column("resource_type", sa.String(16), primary_key=True),
        sa.Column("resource_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "resource_type IN ('dataset', 'job', 'batch')",
            name="ck_resource_ownership_type",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_resource_ownership_user_id", "resource_ownership", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_resource_ownership_user_id", table_name="resource_ownership")
    op.drop_table("resource_ownership")
