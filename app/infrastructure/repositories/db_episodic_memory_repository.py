from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, delete, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.episodic_memory import EpisodicMemory
from app.domain.repositories.episodic_memory_repository import EpisodicMemoryRepository
from app.infrastructure.models.episodic_memory import EpisodicMemoryModel


class DBEpisodicMemoryRepository(EpisodicMemoryRepository):
    """基于 Postgres + pgvector 的情景记忆仓库"""

    def __init__(self, db_session: AsyncSession) -> None:
        """构造函数，完成仓库初始化"""
        self.db_session = db_session

    async def add(self, memory: EpisodicMemory) -> str:
        """写入一条情景记忆"""
        model = EpisodicMemoryModel.from_domain(memory)
        self.db_session.add(model)
        await self.db_session.flush()
        return memory.id

    async def search(
        self,
        query_embedding: List[float],
        agent_name: str,
        top_k: int = 3,
        max_distance: float = 0.6,
    ) -> List[EpisodicMemory]:
        """按向量余弦距离召回相关经验"""
        # pgvector 余弦距离：<=> 算子，distance 越小越相似
        distance = EpisodicMemoryModel.embedding.cosine_distance(query_embedding)
        stmt = (
            select(EpisodicMemoryModel)
            .where(EpisodicMemoryModel.agent_name == agent_name)
            .where(distance < max_distance)
            .order_by(distance.asc())
            .limit(top_k)
        )
        result = await self.db_session.execute(stmt)
        return [model.to_domain() for model in result.scalars().all()]

    async def get_by_id(self, memory_id: str) -> Optional[EpisodicMemory]:
        """根据id获取一条情景记忆"""
        stmt = select(EpisodicMemoryModel).where(EpisodicMemoryModel.id == memory_id)
        result = await self.db_session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_domain() if model else None

    async def increment_use(self, memory_id: str) -> None:
        """使用计数+1并更新最近召回时间"""
        stmt = (
            update(EpisodicMemoryModel)
            .where(EpisodicMemoryModel.id == memory_id)
            .values(
                use_count=EpisodicMemoryModel.use_count + 1,
                last_used_at=datetime.now(),
            )
        )
        await self.db_session.execute(stmt)

    async def delete(self, memory_id: str) -> None:
        """根据id删除一条情景记忆"""
        stmt = delete(EpisodicMemoryModel).where(EpisodicMemoryModel.id == memory_id)
        await self.db_session.execute(stmt)

    async def count_by_agent(self, agent_name: str) -> int:
        """统计某Agent名下的情景记忆数量"""
        stmt = (
            select(func.count(EpisodicMemoryModel.id))
            .where(EpisodicMemoryModel.agent_name == agent_name)
        )
        result = await self.db_session.execute(stmt)
        return result.scalar_one()
