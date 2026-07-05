from typing import List, Optional, Protocol

from app.domain.models.episodic_memory import EpisodicMemory


class EpisodicMemoryRepository(Protocol):
    """情景记忆仓库协议（Postgres + pgvector）"""

    async def add(self, memory: EpisodicMemory) -> str:
        """写入一条情景记忆，返回其id"""
        ...

    async def search(
        self,
        query_embedding: List[float],
        agent_name: str,
        top_k: int = 3,
        max_distance: float = 0.6,
    ) -> List[EpisodicMemory]:
        """按向量余弦距离召回相关经验（distance 越小越相似）"""
        ...

    async def get_by_id(self, memory_id: str) -> Optional[EpisodicMemory]:
        """根据id获取一条情景记忆"""
        ...

    async def increment_use(self, memory_id: str) -> None:
        """使用计数+1并更新最近召回时间"""
        ...

    async def delete(self, memory_id: str) -> None:
        """根据id删除一条情景记忆"""
        ...

    async def count_by_agent(self, agent_name: str) -> int:
        """统计某Agent名下的情景记忆数量"""
        ...
