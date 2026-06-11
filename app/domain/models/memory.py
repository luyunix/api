import logging
from typing import List, Dict, Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class Memory(BaseModel):
    """记忆类，三层架构：System + Working + Episodic

    1. system_messages: 系统提示词，始终保留
    2. working_messages: 当前任务的对话历史（user/assistant/tool）
    3. episodic_notes: 跨会话经验摘要（高价值经验）

    向后兼容：通过 `messages` property 自动合并三层，
    旧代码访问 memory.messages 时透明返回合并列表。
    """
    system_messages: List[Dict[str, Any]] = Field(default_factory=list)
    working_messages: List[Dict[str, Any]] = Field(default_factory=list)
    episodic_notes: List[Dict[str, Any]] = Field(default_factory=list)

    # Token 预算相关(可选,由外部注入)
    _budget_manager: Any = None

    @property
    def messages(self) -> List[Dict[str, Any]]:
        """向后兼容的属性：返回三层合并后的消息列表

        注意：这是一个 property，修改返回列表中的元素会同步影响三层中的原始对象（共享引用）。
        但不能对返回的列表做 append/pop 等操作（不会反映回三层）。
        """
        return self.system_messages + self.episodic_notes + self.working_messages

    def set_budget_manager(self, budget_manager: Any) -> None:
        """设置 Token 预算管理器"""
        self._budget_manager = budget_manager

    @classmethod
    def get_message_role(cls, message: Dict[str, Any]) -> str:
        """根据传递的消息来获取消息的角色信息"""
        return message.get("role")

    def add_message(self, message: Dict[str, Any]) -> None:
        """往记忆中添加一条消息（按角色自动分层）"""
        role = message.get("role", "")
        if role == "system":
            # 区分 system prompt 和 episodic note
            content = message.get("content", "")
            if content.startswith("[经验]"):
                self.episodic_notes.append(message)
            else:
                self.system_messages.append(message)
        else:
            self.working_messages.append(message)

    def add_messages(self, messages: List[Dict[str, Any]]) -> None:
        """往记忆中添加多条消息（按角色自动分层）"""
        for msg in messages:
            self.add_message(msg)

    def get_messages(self) -> List[Dict[str, Any]]:
        """获取记忆中的所有消息列表（三层合并）

        返回顺序: system → episodic_notes → working
        episodic_notes 插入在 system 和 working 之间，
        让 LLM 先看到经验，再看到当前对话。
        """
        return self.system_messages + self.episodic_notes + self.working_messages

    def get_last_message(self) -> Optional[Dict[str, Any]]:
        """获取记忆中的最后一条消息，如果不存在则返回None"""
        all_messages = self.get_messages()
        return all_messages[-1] if len(all_messages) > 0 else None

    def roll_back(self) -> None:
        """回滚记忆，删除 working_messages 的最后一条消息"""
        if self.working_messages:
            self.working_messages = self.working_messages[:-1]

    @classmethod
    def _from_legacy_messages(cls, messages: List[Dict[str, Any]]) -> "Memory":
        """从旧格式的 messages 列表迁移到新格式"""
        system_messages = []
        working_messages = []
        for msg in messages:
            role = msg.get("role", "")
            if role == "system":
                system_messages.append(msg)
            else:
                working_messages.append(msg)
        return cls(
            system_messages=system_messages,
            working_messages=working_messages,
            episodic_notes=[],
        )

    def add_episodic_note(self, note: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """添加跨会话经验摘要

        :param note: 经验文本
        :param metadata: 可选元数据（如 task_type, keywords 等）
        """
        self.episodic_notes.append({
            "role": "system",
            "content": f"[经验] {note}",
            "metadata": metadata or {},
        })
        logger.info(f"Memory 添加 episodic_note: {note[:50]}...")

    def compact(self) -> None:
        """记忆压缩

        如果配置了 MemoryBudgetManager，则使用智能预算压缩；
        否则使用传统的固定策略压缩。

        压缩只作用于 working_messages，system 和 episodic 不会被压缩。
        因为 get_messages() 返回的列表元素是原始对象的引用，
        budget_manager 对列表元素的修改会直接反映到 working_messages 中。
        """
        if self._budget_manager:
            # 传入 self，budget_manager 访问 self.messages 获取合并列表
            compacted = self._budget_manager.check_and_compact(self)
            if compacted:
                return

        self._compact_legacy()

    def _compact_legacy(self) -> None:
        """传统固定策略压缩（仅操作 working_messages）"""
        for message in self.working_messages:
            if self.get_message_role(message) == "tool":
                if message.get("function_name") in ["browser_view", "browser_navigate"]:
                    message["content"] = "(removed)"
                    logger.debug(f"从记忆中移除对应工具的结果: {message['function_name']}")

            if "reasoning_content" in message:
                logger.debug(f"从记忆中移除工具思考结果: {message['reasoning_content'][:50]}...")
                del message["reasoning_content"]

    @property
    def empty(self) -> bool:
        """只读属性，检查记忆是否为空"""
        return len(self.system_messages) == 0 and len(self.working_messages) == 0

    def to_legacy_dict(self) -> Dict[str, Any]:
        """转换为新格式字典（用于数据库写入）"""
        return {
            "system_messages": self.system_messages,
            "working_messages": self.working_messages,
            "episodic_notes": self.episodic_notes,
        }
