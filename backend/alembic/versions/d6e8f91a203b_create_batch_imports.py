"""Track direct batch uploads and asynchronous imports."""

import sqlalchemy as sa

from alembic import op

revision = "d6e8f91a203b"
down_revision = "c87401d2a161"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "batch_imports",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("upload_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("etag", sa.String(256)),
        sa.Column("claim_token", sa.Uuid(as_uuid=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("batch_id", sa.Uuid(as_uuid=True)),
        sa.Column("error_message", sa.Text()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_batch_imports_status_created", "batch_imports", ["status", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_batch_imports_status_created", table_name="batch_imports")
    op.drop_table("batch_imports")
