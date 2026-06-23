"""create episodic_memories table (pgvector)

Revision ID: e4c1f7a92b3d
Revises: 0e0d242438bc
Create Date: 2026-06-23 00:00:00.000000

情景记忆表，用于跨会话经验学习。依赖 pgvector 扩展，
embedding 列为 1024 维向量（与 EmbeddingConfig.dimension 一致）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = 'e4c1f7a92b3d'
down_revision: Union[str, Sequence[str], None] = '0e0d242438bc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 向量维度，需与 EmbeddingConfig.dimension / EpisodicMemoryModel.EMBEDDING_DIMENSION 一致
EMBEDDING_DIMENSION = 1024


def upgrade() -> None:
    """Upgrade schema."""
    # 1.启用 pgvector 扩展（需 Postgres 已安装 pgvector，如 pgvector/pgvector 镜像）
    op.execute('CREATE EXTENSION IF NOT EXISTS vector;')

    # 2.创建情景记忆表
    op.create_table(
        'episodic_memories',
        sa.Column('id', sa.String(length=255), nullable=False),
        sa.Column('agent_name', sa.String(length=64), nullable=False),
        sa.Column('source_session', sa.String(length=255), nullable=True),
        sa.Column('summary', sa.String(length=512), server_default=sa.text("''::character varying"),
                  nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('metadata', sa.dialects.postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"),
                  nullable=False),
        sa.Column('importance', sa.Float(), server_default=sa.text('0.5'), nullable=False),
        sa.Column('embedding', Vector(EMBEDDING_DIMENSION), nullable=False),
        sa.Column('use_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP(0)'),
                  nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_episodic_memories_id'),
    )

    # 3.普通索引（按 agent_name 过滤）
    op.create_index('ix_episodic_memories_agent_name', 'episodic_memories', ['agent_name'])

    # 4.HNSW 向量索引（余弦相似度），加速召回
    op.create_index(
        'ix_episodic_memories_embedding',
        'episodic_memories',
        ['embedding'],
        postgresql_using='hnsw',
        postgresql_with={'m': 16, 'ef_construction': 64},
        postgresql_ops={'embedding': 'vector_cosine_ops'},
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_episodic_memories_embedding', table_name='episodic_memories')
    op.drop_index('ix_episodic_memories_agent_name', table_name='episodic_memories')
    op.drop_table('episodic_memories')
    # 注意：不自动 DROP vector 扩展，避免影响其它可能依赖它的对象
