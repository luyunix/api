import logging
from typing import Dict, Any, List

from app.domain.models.memory import Memory
from .token_counter import TokenCounter

logger = logging.getLogger(__name__)


class MemoryBudgetManager:
    """记忆预算管理器

    负责监控记忆的 Token 使用量,当接近模型上下文上限时,
    按消息价值排序触发压缩,优先删除低价值消息。
    """

    # 压缩触发阈值(占预算的百分比)
    COMPACT_SOFT_THRESHOLD = 0.70   # 70%: 记录警告日志
    COMPACT_HARD_THRESHOLD = 0.85   # 85%: 触发自动压缩
    COMPACT_EMERGENCY_THRESHOLD = 0.95  # 95%: 紧急压缩(删除更多)

    # 消息价值权重(越小越容易被压缩)
    MESSAGE_VALUE_WEIGHTS = {
        "system": 100,           # 系统提示词: 绝对不能删
        "user": 80,              # 用户消息: 非常重要
        "assistant": 70,         # AI 消息(无工具调用): 重要
        "tool_summary": 60,      # 工具调用摘要(压缩后)
        "assistant_tool_call": 50,  # AI 工具调用请求
        "tool_generic": 30,      # 普通工具结果(search, shell等)
        "tool_browser": 10,      # 浏览器结果: 最长最占Token,优先删
    }

    def __init__(self, budget: int = 8000):
        """构造函数

        :param budget: Token 预算上限,默认 8000(DeepSeek chat 模型的安全值)
        """
        self._budget = budget
        self._current_tokens = 0
        self._last_compact_index = -1  # 上次压缩到的消息索引

    @property
    def budget(self) -> int:
        return self._budget

    @property
    def current_tokens(self) -> int:
        return self._current_tokens

    @property
    def usage_percentage(self) -> float:
        return self._current_tokens / self._budget if self._budget > 0 else 0

    @property
    def remaining(self) -> int:
        return max(0, self._budget - self._current_tokens)

    def check_and_compact(self, memory: Memory) -> bool:
        """检查 Token 预算并触发压缩

        :param memory: 当前记忆
        :return: 是否执行了压缩
        """
        self._current_tokens = TokenCounter.count_messages(memory.messages)
        percentage = self.usage_percentage

        logger.debug(
            f"MemoryBudget 检查: {TokenCounter.format_budget_status(self._current_tokens, self._budget)}"
        )

        if percentage >= self.COMPACT_EMERGENCY_THRESHOLD:
            logger.warning(
                f"MemoryBudget 紧急压缩! {TokenCounter.format_budget_status(self._current_tokens, self._budget)}"
            )
            self._compact_by_value(memory, aggressive=True)
            return True

        elif percentage >= self.COMPACT_HARD_THRESHOLD:
            logger.info(
                f"MemoryBudget 触发压缩 {TokenCounter.format_budget_status(self._current_tokens, self._budget)}"
            )
            self._compact_by_value(memory, aggressive=False)
            return True

        elif percentage >= self.COMPACT_SOFT_THRESHOLD:
            logger.info(
                f"MemoryBudget 接近上限 {TokenCounter.format_budget_status(self._current_tokens, self._budget)}"
            )

        return False

    def _compact_by_value(self, memory: Memory, aggressive: bool = False) -> None:
        """按消息价值排序压缩记忆

        策略:
        1. 遍历所有消息,给每条消息打分(价值 × Token数)
        2. 优先压缩低价值且高 Token 的消息
        3. 保留最近的对话上下文
        """
        messages = memory.messages
        if not messages:
            return

        # 计算每条消息的价值分(越低越容易被压缩)
        scored_messages: List[Dict[str, Any]] = []
        for idx, message in enumerate(messages):
            tokens = TokenCounter.count_message(message)
            value = self._calculate_message_value(message, idx, len(messages))
            score = value / max(tokens, 1)  # 价值/Token比
            scored_messages.append({
                "index": idx,
                "message": message,
                "tokens": tokens,
                "value": value,
                "score": score,
            })

        # 按 score 排序(升序),score 最低的先被压缩
        scored_messages.sort(key=lambda x: x["score"])

        target_usage = self._budget * 0.60 if aggressive else self._budget * 0.70
        current = self._current_tokens
        compacted_count = 0

        for item in scored_messages:
            if current <= target_usage:
                break

            idx = item["index"]
            message = item["message"]
            role = message.get("role", "")
            func_name = message.get("function_name", "")

            # 永远不压缩系统提示词和最近2条消息
            if role == "system":
                continue
            if idx >= len(messages) - 2:
                continue

            # 已经压缩过的跳过
            if role == "tool" and message.get("content") == "(removed)":
                continue

            # 执行压缩
            old_content = message.get("content", "")
            old_tokens = item["tokens"]

            if role == "tool":
                if func_name in ["browser_view", "browser_navigate"]:
                    message["content"] = "(removed)"
                elif aggressive:
                    # 激进模式: 所有工具结果都替换为摘要
                    message["content"] = f"(result: {str(old_content)[:50]}...)"
                else:
                    message["content"] = "(removed)"
            elif role == "assistant" and not message.get("tool_calls"):
                # AI 的普通回复: 保留前 200 字符摘要
                if aggressive and len(old_content) > 200:
                    message["content"] = old_content[:200] + "...(truncated)"

            # 删除 reasoning_content（先计算 Token 再删除）
            reasoning_content = message.get("reasoning_content", "")
            if reasoning_content:
                current -= TokenCounter._estimate_text_tokens(reasoning_content)
                del message["reasoning_content"]

            new_tokens = TokenCounter.count_message(message)
            saved = old_tokens - new_tokens
            current -= saved
            compacted_count += 1

            logger.debug(
                f"MemoryBudget 压缩消息[{idx}] role={role}: "
                f"节省 {saved} tokens, 剩余 {current}/{self._budget}"
            )

        self._current_tokens = current
        self._last_compact_index = len(messages) - 1

        logger.info(
            f"MemoryBudget 压缩完成: 压缩 {compacted_count} 条消息, "
            f"当前 {TokenCounter.format_budget_status(self._current_tokens, self._budget)}"
        )

    def _calculate_message_value(self, message: Dict[str, Any], index: int, total: int) -> float:
        """计算单条消息的价值分数

        考虑因素:
        - 消息类型(role)
        - 是否包含工具调用
        - 在对话中的位置(越新的消息价值越高)
        """
        role = message.get("role", "")
        base_value = self.MESSAGE_VALUE_WEIGHTS.get(role, 50)

        # 工具消息进一步细分
        if role == "tool":
            func_name = message.get("function_name", "")
            if func_name in ["browser_view", "browser_navigate"]:
                base_value = self.MESSAGE_VALUE_WEIGHTS["tool_browser"]
            elif func_name == "message_ask_user":
                base_value = 70  # 询问用户的结果很重要
            else:
                base_value = self.MESSAGE_VALUE_WEIGHTS["tool_generic"]

        # 已经被压缩过的消息价值更低
        content = message.get("content", "")
        if content == "(removed)" or content.endswith("...(truncated)"):
            base_value *= 0.5

        # 位置因子: 越新的消息价值越高
        recency_factor = 1.0 + (index / max(total, 1)) * 0.5

        return base_value * recency_factor

    def get_budget_report(self) -> Dict[str, Any]:
        """获取预算状态报告"""
        return {
            "budget": self._budget,
            "current": self._current_tokens,
            "remaining": self.remaining,
            "usage_percentage": round(self.usage_percentage * 100, 1),
            "status": (
                "emergency" if self.usage_percentage >= self.COMPACT_EMERGENCY_THRESHOLD
                else "hard" if self.usage_percentage >= self.COMPACT_HARD_THRESHOLD
                else "soft" if self.usage_percentage >= self.COMPACT_SOFT_THRESHOLD
                else "ok"
            ),
        }
