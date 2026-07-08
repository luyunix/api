"""add users and owner fields

Revision ID: 7b2d4f1a9c20
Revises: e4c1f7a92b3d
Create Date: 2026-07-09 01:10:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7b2d4f1a9c20"
down_revision: Union[str, Sequence[str], None] = "e4c1f7a92b3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("avatar_url", sa.String(length=1024), server_default=sa.text("''::character varying"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP(0)"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP(0)"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users_id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.add_column("sessions", sa.Column("user_id", sa.String(length=255), nullable=True))
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    op.add_column("files", sa.Column("user_id", sa.String(length=255), nullable=True))
    op.create_index("ix_files_user_id", "files", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_files_user_id", table_name="files")
    op.drop_column("files", "user_id")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_column("sessions", "user_id")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
