import logging
from typing import Dict, Any, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class Memory(BaseModel):
    """记忆类，三层架构：System + Working + Episodic

    1. system_messages: 系统提示词，始终保留，永不压缩
    2. working_messages: 当前任务的对话历史（user/assistant/tool），按 token 预算压缩
    3. episodic_notes: 跨会话经验（每轮从 pgvector 召回注入，瞬态，不持久化）

    设计原则（v2 重写）：
    - 纯数据模型：不持有 budget_manager / 不自己做压缩，压缩交给外部 MemoryCompactor
    - 显式分段：add_message 按 role 分流（system→system_messages，其余→working_messages），
      不再用 "[经验]" 字符串嗅探；episodic 只通过 add_episodic_note 显式写入
    - 无共享引用陷阱：get_messages() 返回全新列表；压缩通过 replace_working 受控改写
    - episodic 不持久化：to_dict() 只序列化 system + working，每轮从 pgvector 重新召回
    """
    system_messages: List[Dict[str, Any]] = Field(default_factory=list)
    working_messages: List[Dict[str, Any]] = Field(default_factory=list)
    episodic_notes: List[Dict[str, Any]] = Field(default_factory=list)

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #
    def add_message(self, message: Dict[str, Any]) -> None:
        """往记忆中添加一条消息（按 role 分流到 system / working）"""
        role = message.get("role", "")
        if role == "system":
            self.system_messages.append(message)
        else:
            self.working_messages.append(message)

    def add_messages(self, messages: List[Dict[str, Any]]) -> None:
        """往记忆中添加多条消息"""
        for msg in messages:
            self.add_message(msg)

    def add_episodic_note(self, note: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """显式添加一条跨会话经验（由 EpisodicMemoryService 召回后注入）

        episodic 是瞬态的，不参与持久化（to_dict 不含 episodic_notes）。
        """
        self.episodic_notes.append({
            "role": "system",
            "content": f"[经验] {note}",
            "metadata": metadata or {},
        })
        logger.debug(f"Memory 添加 episodic_note: {note[:50]}...")

    def replace_working(self, new_working: List[Dict[str, Any]]) -> None:
        """受控改写 working 段（供 MemoryCompactor 压缩后回写使用）"""
        self.working_messages = list(new_working)

    def roll_back(self) -> None:
        """删除 working_messages 的最后一条消息"""
        if self.working_messages:
            self.working_messages = self.working_messages[:-1]

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #
    @property
    def messages(self) -> List[Dict[str, Any]]:
        """三层合并视图：system → episodic → working"""
        return self.system_messages + self.episodic_notes + self.working_messages

    def get_messages(self) -> List[Dict[str, Any]]:
        """返回一个全新的合并列表（system → episodic → working）

        返回新列表本身；元素 dict 仍为原始引用，但本类承诺不会通过
        返回值就地修改消息——所有压缩改写都走 replace_working。
        """
        return list(self.messages)

    def get_last_message(self) -> Optional[Dict[str, Any]]:
        """获取记忆中的最后一条消息，如果不存在则返回None"""
        all_messages = self.messages
        return all_messages[-1] if len(all_messages) > 0 else None

    @property
    def empty(self) -> bool:
        """只读属性，检查记忆是否为空（三段全空才算空）"""
        return not (self.system_messages or self.working_messages or self.episodic_notes)

    # ------------------------------------------------------------------ #
    # 持久化（只存 system + working；episodic 瞬态不持久化）
    # ------------------------------------------------------------------ #
    def to_dict(self) -> Dict[str, Any]:
        """序列化为持久化字典（不含 episodic_notes）"""
        return {
            "system_messages": self.system_messages,
            "working_messages": self.working_messages,
        }

    # 向后兼容别名：旧调用方使用 to_legacy_dict()
    to_legacy_dict = to_dict

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]] = None) -> "Memory":
        """从持久化字典构建 Memory，兼容三种历史格式

        - 旧旧格式 {"messages": [...]}         → 全部按 role 拆分
        - 旧格式   {system_messages, working_messages, episodic_notes} → 忽略 episodic（瞬态）
        - 新格式   {system_messages, working_messages}
        """
        if not data:
            return cls()

        # 旧旧格式：只有 messages 字段
        if "messages" in data and "system_messages" not in data:
            logger.info(f"检测到旧格式记忆(只有messages)，自动迁移")
            return cls._from_legacy_messages(data.get("messages", []))

        # 新/旧格式：都有 system_messages（episodic 不从 DB 读）
        return cls(
            system_messages=data.get("system_messages", []),
            working_messages=data.get("working_messages", []),
            episodic_notes=[],
        )

    @classmethod
    def _from_legacy_messages(cls, messages: List[Dict[str, Any]]) -> "Memory":
        """从旧格式的 messages 列表迁移到新格式（按 role 拆分）"""
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
