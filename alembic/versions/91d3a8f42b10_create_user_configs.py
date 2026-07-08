"""create user configs table

Revision ID: 91d3a8f42b10
Revises: 7b2d4f1a9c20
Create Date: 2026-07-09 01:20:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "91d3a8f42b10"
down_revision: Union[str, Sequence[str], None] = "7b2d4f1a9c20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_configs",
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("config", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP(0)"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP(0)"), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_configs_user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_configs")
