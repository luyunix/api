import logging
from typing import Dict, Any, List, Optional

from app.domain.services.memory.vector_memory import VectorMemory

logger = logging.getLogger(__name__)


class MemoryRetriever:
    """记忆检索器

    负责将用户查询转化为向量检索,
    并将检索结果格式化为 episodic_notes 注入记忆。

    工作流程:
    1. 用户发送新消息
    2. MemoryRetriever 用消息内容检索历史记忆
    3. 找到相关历史后,格式化为 "[经验] 曾经..." 的笔记
    4. 将笔记添加到 Memory.episodic_notes
    """

    def __init__(self, session_id: str, top_k: int = 3, similarity_threshold: float = 0.15):
        """构造函数

        :param session_id: 会话 ID
        :param top_k: 每次检索返回的最相关条目数
        :param similarity_threshold: 相似度阈值
        """
        self._session_id = session_id
        self._top_k = top_k
        self._similarity_threshold = similarity_threshold
        # 为 planner 和 react 各创建一个 VectorMemory
        self._planner_vm = VectorMemory(
            session_id=session_id,
            agent_name="planner",
            top_k=top_k,
            similarity_threshold=similarity_threshold,
        )
        self._react_vm = VectorMemory(
            session_id=session_id,
            agent_name="react",
            top_k=top_k,
            similarity_threshold=similarity_threshold,
        )

    async def retrieve_for_planner(self, query: str) -> List[Dict[str, Any]]:
        """为 PlannerAgent 检索相关历史经验"""
        return await self._planner_vm.search(query)

    async def retrieve_for_react(self, query: str) -> List[Dict[str, Any]]:
        """为 ReActAgent 检索相关历史经验"""
        return await self._react_vm.search(query)

    async def index_planner_memory(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """将 PlannerAgent 的历史记忆索引到向量库"""
        if text and len(text.strip()) > 10:  # 只索引有意义的文本
            await self._planner_vm.add(text, metadata)

    async def index_react_memory(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """将 ReActAgent 的历史记忆索引到向量库"""
        if text and len(text.strip()) > 10:
            await self._react_vm.add(text, metadata)

    def format_as_episodic_note(self, result: Dict[str, Any]) -> str:
        """将检索结果格式化为 episodic_note 文本

        示例输出:
        "曾经处理过类似需求: 用户要求查询天气并生成报告。当时的做法是..."
        """
        text = result.get("text", "")
        similarity = result.get("similarity", 0)
        metadata = result.get("metadata", {})

        # 提取核心内容(避免太长的原文)
        excerpt = text[:200] + "..." if len(text) > 200 else text

        note = f"曾经处理过类似需求(相关度{similarity:.0%}): {excerpt}"

        # 如果有额外上下文,添加
        if metadata.get("tool_name"):
            note += f" [使用了 {metadata['tool_name']}]"

        return note

    async def clear_all(self) -> None:
        """清空当前会话的所有向量记忆"""
        await self._planner_vm.clear()
        await self._react_vm.clear()
