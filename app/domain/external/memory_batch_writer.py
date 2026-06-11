from typing import Protocol

from app.domain.models.memory import Memory


class MemoryBatchWriter(Protocol):
    """记忆批量写入器协议

    用于将记忆写入操作从同步阻塞改为异步批量,
    减少高频对话场景下的数据库压力。
    """

    async def enqueue(self, session_id: str, agent_name: str, memory: Memory) -> None:
        """将记忆写入请求放入队列,不立即持久化"""
        ...

    async def flush(self) -> None:
        """立即将队列中的所有记忆写入持久化存储"""
        ...

    async def start(self) -> None:
        """启动后台刷新任务"""
        ...

    async def shutdown(self) -> None:
        """优雅关闭,刷新剩余队列后停止"""
        ...
