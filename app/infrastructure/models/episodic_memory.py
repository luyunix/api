import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Float, DateTime, Text, text, PrimaryKeyConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from .base import Base

# 向量维度：必须与 EmbeddingConfig.dimension（默认 1024）保持一致。
# 修改维度需要新建 Alembic 迁移并重新生成已有向量。
EMBEDDING_DIMENSION = 1024


class EpisodicMemoryModel(Base):
    """情景记忆ORM模型（pgvector 向量列）"""
    __tablename__ = "episodic_memories"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_episodic_memories_id"),
    )

    id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )  # 记忆id
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # 归属Agent
    source_session: Mapped[str] = mapped_column(String(255), nullable=True)  # 源会话id
    summary: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        server_default=text("''::character varying"),
    )  # 一句话标题
    content: Mapped[str] = mapped_column(Text, nullable=False)  # 完整经验文本
    # metadata 是 SQLAlchemy 保留属性名，故 Python 属性用 metadata_，映射到 DB 列 metadata
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )  # 元数据
    importance: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        server_default=text("0.5"),
    )  # 重要性
    embedding: Mapped[list] = mapped_column(Vector(EMBEDDING_DIMENSION), nullable=False)  # 向量
    use_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )  # 使用计数
    last_used_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)  # 最近召回时间
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )  # 创建时间

    @classmethod
    def from_domain(cls, memory: "EpisodicMemory") -> "EpisodicMemoryModel":
        """从领域模型构建ORM模型"""
        return cls(
            id=memory.id,
            agent_name=memory.agent_name,
            source_session=memory.source_session,
            summary=memory.summary,
            content=memory.content,
            metadata_=memory.metadata,
            importance=memory.importance,
            embedding=memory.embedding,
            use_count=memory.use_count,
            last_used_at=memory.last_used_at,
        )

    def to_domain(self) -> "EpisodicMemory":
        """将ORM模型转换成领域模型"""
        from app.domain.models.episodic_memory import EpisodicMemory
        return EpisodicMemory(
            id=self.id,
            agent_name=self.agent_name,
            source_session=self.source_session,
            summary=self.summary,
            content=self.content,
            metadata=self.metadata_,
            importance=self.importance,
            embedding=list(self.embedding) if self.embedding is not None else None,
            use_count=self.use_count,
            last_used_at=self.last_used_at,
            created_at=self.created_at,
        )
