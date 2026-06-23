import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional

from pydantic import BaseModel, Field


class EpisodicMemory(BaseModel):
    """情景记忆领域模型：一条跨会话的可复用经验

    由任务完成后 EpisodicMemoryService 提炼并写入 pgvector，
    下次相似任务通过向量召回注入到记忆的 episodic 段。
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))  # 唯一标识
    agent_name: str = ""  # 归属Agent：planner | react | global
    source_session: Optional[str] = None  # 产生该经验的源会话id
    summary: str = ""  # 一句话标题
    content: str = ""  # 完整经验文本（召回与展示用）
    metadata: Dict[str, Any] = Field(default_factory=dict)  # goal / tools / success / tags 等
    importance: float = 0.5  # 重要性 0~1，影响召回权重与淘汰
    embedding: Optional[List[float]] = None  # 向量（写入前由 Embedder 生成）
    use_count: int = 0  # 被召回使用次数
    last_used_at: Optional[datetime] = None  # 最近一次被召回时间
    created_at: Optional[datetime] = None  # 创建时间
