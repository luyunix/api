import logging
from typing import Optional, Dict, Any, List

from app.domain.models.memory import Memory
from app.domain.services.memory.memory_summarizer import MemorySummarizer
from app.domain.services.memory.token_counter import TokenCounter

logger = logging.getLogger(__name__)


class MemoryCompactor:
    """记忆压缩器

    监控记忆 token 用量，接近上下文上限时按「消息价值/token」排序压缩
    低价值消息。压缩只作用于 working 段（system 永不压缩，episodic 瞬态不管）。

    v2 重写要点（相对旧 MemoryBudgetManager）：
    - token 用 tiktoken 精确计数（TokenCounter），而非启发式高估
    - 预算基于 usable_context（context_window - 输出 - 预留），而非 max_tokens
    - 压缩不再就地修改消息 dict，而是构建新的 working 列表后 replace_working 回写
    - 压缩时调用 MemorySummarizer 生成摘要，替代粗暴的 "(removed)"
    - Planner / ReAct 共用同一压缩路径
    """

    # 压缩触发阈值（占可用上下文的百分比）
    COMPACT_SOFT_THRESHOLD = 0.70      # 70%: 仅记录日志
    COMPACT_HARD_THRESHOLD = 0.85      # 85%: 触发压缩
    COMPACT_EMERGENCY_THRESHOLD = 0.95  # 95%: 紧急（更激进）压缩

    # 消息价值权重（越小越容易被压缩）
    MESSAGE_VALUE_WEIGHTS = {
        "system": 100,             # 系统提示词（实际不在 working 内，预留）
        "user": 80,                # 用户消息：非常重要
        "assistant": 70,           # AI 普通回复
        "assistant_tool_call": 50,  # AI 工具调用请求
        "tool_generic": 30,        # 普通工具结果（search/shell 等）
        "tool_browser": 10,        # 浏览器结果：最长最占 token，优先压缩
    }

    # 受保护：working 段最后 N 条不压缩
    RESERVE_RECENT = 2

    def __init__(
            self,
            usable_context: int,
            summarizer: Optional[MemorySummarizer] = None,
            soft: float = COMPACT_SOFT_THRESHOLD,
            hard: float = COMPACT_HARD_THRESHOLD,
            emergency: float = COMPACT_EMERGENCY_THRESHOLD,
    ) -> None:
        """构造函数

        :param usable_context: 可用上下文 token 数（context_window - max_tokens - reserve）
        :param summarizer: 可选的记忆摘要器，压缩时生成 LLM 摘要
        """
        self._usable_context = usable_context
        self._summarizer = summarizer
        self._soft = soft
        self._hard = hard
        self._emergency = emergency
        self._current_tokens = 0

    @property
    def budget(self) -> int:
        """兼容旧字段名，返回可用上下文预算"""
        return self._usable_context

    @property
    def current_tokens(self) -> int:
        return self._current_tokens

    @property
    def usage_percentage(self) -> float:
        return self._current_tokens / self._usable_context if self._usable_context > 0 else 0

    @property
    def remaining(self) -> int:
        return max(0, self._usable_context - self._current_tokens)

    async def compact(self, memory: Memory, model_name: Optional[str] = None) -> bool:
        """检查 token 预算并在超阈值时压缩，返回是否执行了压缩"""
        # 1.精确计数（tiktoken）
        self._current_tokens = TokenCounter.count_messages(memory.get_messages(), model_name)
        percentage = self.usage_percentage

        logger.debug(
            f"MemoryCompactor 检查: {TokenCounter.format_budget_status(self._current_tokens, self._usable_context)}"
        )

        if percentage >= self._emergency:
            logger.warning(
                f"MemoryCompactor 紧急压缩! {TokenCounter.format_budget_status(self._current_tokens, self._usable_context)}"
            )
            await self._compact_working(memory, aggressive=True, model_name=model_name)
            return True

        if percentage >= self._hard:
            logger.info(
                f"MemoryCompactor 触发压缩 {TokenCounter.format_budget_status(self._current_tokens, self._usable_context)}"
            )
            await self._compact_working(memory, aggressive=False, model_name=model_name)
            return True

        if percentage >= self._soft:
            logger.info(
                f"MemoryCompactor 接近上限 {TokenCounter.format_budget_status(self._current_tokens, self._usable_context)}"
            )

        return False

    # 兼容旧方法名（部分调用方可能仍用 check_and_compact）
    async def check_and_compact(self, memory: Memory, model_name: Optional[str] = None) -> bool:
        return await self.compact(memory, model_name)

    async def _compact_working(self, memory: Memory, aggressive: bool, model_name: Optional[str]) -> None:
        """按价值排序压缩 working 段，构建新列表后 replace_working 回写

        策略：
        1. 对 working 每条消息打分（价值 × 位置 / token），分数低优先压缩
        2. 压缩目标降到预算的 60%（激进）/ 70%（普通）
        3. 最近 RESERVE_RECENT 条与 system 段受保护
        4. 被压缩的 tool 结果用 summarizer 生成摘要，长 assistant 文本截断
        """
        working = memory.working_messages
        n = len(working)
        if n == 0:
            return

        # 1.打分（升序：分数最低的最先压缩）
        scored: List[Dict[str, Any]] = []
        for idx, message in enumerate(working):
            tokens = TokenCounter.count_message(message, model_name)
            value = self._calculate_message_value(message, idx, n)
            score = value / max(tokens, 1)
            scored.append({"index": idx, "message": message, "tokens": tokens, "score": score})
        scored.sort(key=lambda x: x["score"])

        target_usage = self._usable_context * (0.60 if aggressive else 0.70)
        current = self._current_tokens

        # 2.构建新 working 列表（浅拷贝；只替换被压缩条目的整条 dict）
        new_working = list(working)
        compacted_count = 0

        for item in scored:
            if current <= target_usage:
                break

            idx = item["index"]
            message = item["message"]

            # 受保护：最近 N 条
            if idx >= n - self.RESERVE_RECENT:
                continue

            role = message.get("role", "")
            replacement: Optional[Dict[str, Any]] = None

            if role == "tool":
                # 工具结果：生成摘要（summarizer 失败则截断）
                context = message.get("function_name", "")
                summarized = await self._summarize_content(message.get("content", ""), context)
                replacement = {**message, "content": summarized}
            elif role == "assistant" and not message.get("tool_calls"):
                # AI 普通回复：激进模式下截断长文本
                old_content = message.get("content", "")
                if aggressive and len(old_content) > 200:
                    replacement = {**message, "content": old_content[:200] + "...(truncated)"}

            if replacement is None:
                continue

            # 删除 reasoning_content（压缩思考过程）
            if "reasoning_content" in replacement:
                del replacement["reasoning_content"]

            new_tokens = TokenCounter.count_message(replacement, model_name)
            saved = item["tokens"] - new_tokens
            current -= saved
            new_working[idx] = replacement
            compacted_count += 1

            logger.debug(
                f"MemoryCompactor 压缩 working[{idx}] role={role}: 节省 {saved} tokens"
            )

        # 3.受控回写
        memory.replace_working(new_working)
        self._current_tokens = current

        logger.info(
            f"MemoryCompactor 压缩完成: 压缩 {compacted_count} 条, "
            f"当前 {TokenCounter.format_budget_status(self._current_tokens, self._usable_context)}"
        )

    async def _summarize_content(self, content: str, context: str) -> str:
        """生成压缩摘要，失败回退截断"""
        if not content:
            return "(empty)"
        if content in ("(removed)", "(empty)"):
            return content
        if self._summarizer:
            try:
                return await self._summarizer.summarize(content, context)
            except Exception as e:
                logger.warning(f"MemoryCompactor 生成摘要失败，回退截断: {e}")
        # 回退：截断
        return content[:200] + "...(truncated)" if len(content) > 200 else content

    def _calculate_message_value(self, message: Dict[str, Any], index: int, total: int) -> float:
        """计算单条消息的价值分数（越低越容易被压缩）"""
        role = message.get("role", "")
        base_value = self.MESSAGE_VALUE_WEIGHTS.get(role, 50)

        # 工具消息按工具类型细分
        if role == "tool":
            func_name = message.get("function_name", "")
            if func_name in ["browser_view", "browser_navigate"]:
                base_value = self.MESSAGE_VALUE_WEIGHTS["tool_browser"]
            elif func_name == "message_ask_user":
                base_value = 70
            else:
                base_value = self.MESSAGE_VALUE_WEIGHTS["tool_generic"]

        # AI 带工具调用的消息价值更高
        if role == "assistant" and message.get("tool_calls"):
            base_value = self.MESSAGE_VALUE_WEIGHTS["assistant_tool_call"]

        # 已被压缩过的消息价值更低（可重复压缩）
        content = message.get("content", "")
        if content in ("(removed)", "(empty)") or content.endswith("...(truncated)"):
            base_value *= 0.5

        # 位置因子：越新价值越高
        recency_factor = 1.0 + (index / max(total, 1)) * 0.5
        return base_value * recency_factor

    def get_budget_report(self) -> Dict[str, Any]:
        """获取预算状态报告"""
        return {
            "budget": self._usable_context,
            "current": self._current_tokens,
            "remaining": self.remaining,
            "usage_percentage": round(self.usage_percentage * 100, 1),
            "status": (
                "emergency" if self.usage_percentage >= self._emergency
                else "hard" if self.usage_percentage >= self._hard
                else "soft" if self.usage_percentage >= self._soft
                else "ok"
            ),
        }


class MemoryBudgetManager(MemoryCompactor):
    """旧版预算管理器兼容层。

    新代码使用 MemoryCompactor；保留这个类名让旧测试和旧调用方继续工作。
    """

    def __init__(
            self,
            budget: int,
            summarizer: Optional[MemorySummarizer] = None,
            soft: float = MemoryCompactor.COMPACT_SOFT_THRESHOLD,
            hard: float = MemoryCompactor.COMPACT_HARD_THRESHOLD,
            emergency: float = MemoryCompactor.COMPACT_EMERGENCY_THRESHOLD,
    ) -> None:
        super().__init__(
            usable_context=budget,
            summarizer=summarizer,
            soft=soft,
            hard=hard,
            emergency=emergency,
        )

    def check_and_compact(self, memory: Memory, model_name: Optional[str] = None) -> bool:
        self._current_tokens = TokenCounter.count_messages(memory.get_messages(), model_name)
        return self.usage_percentage >= self._hard
