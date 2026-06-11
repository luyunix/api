import logging
from typing import Dict, Any, Optional

from app.domain.external.llm import LLM

logger = logging.getLogger(__name__)


class MemorySummarizer:
    """记忆摘要器

    使用 LLM 对压缩后的消息生成智能摘要,
    替代粗暴的 "(removed)" 或 "...(truncated)"。

    设计原则:
    - 轻量: prompt 简短,减少 Token 消耗
    - 异步: 在 compact 之后后台执行,不阻塞主流程
    - 容错: LLM 调用失败时回退到截断策略
    """

    def __init__(self, llm: LLM, max_input_length: int = 1500, max_output_length: int = 200):
        """构造函数

        :param llm: 语言模型实例
        :param max_input_length: 输入内容最大长度(截断前)
        :param max_output_length: 摘要最大长度
        """
        self._llm = llm
        self._max_input_length = max_input_length
        self._max_output_length = max_output_length

    async def summarize(self, content: str, context: Optional[str] = None) -> str:
        """生成内容摘要

        :param content: 需要摘要的原始内容
        :param context: 可选上下文（如工具名）
        :return: 摘要文本
        """
        if not content or len(content.strip()) == 0:
            return "(empty)"

        # 如果内容本身就很短,直接保留
        if len(content) <= self._max_output_length:
            return content

        # 截断输入避免 Token 爆炸
        truncated = content[:self._max_input_length]
        if len(content) > self._max_input_length:
            truncated += "..."

        context_hint = f"({context})" if context else ""

        prompt = f"""请用一句话总结以下内容的关键信息{context_hint}:

{truncated}

要求:
- 保留所有关键数据、结论和事实
- 删除冗余描述和格式
- 长度不超过 200 字
- 直接输出摘要,不要解释"""

        try:
            result = await self._llm.invoke(
                messages=[{"role": "user", "content": prompt}],
            )
            summary = result.get("content", "")

            # 清理输出
            summary = summary.strip().strip('"').strip("'")
            if summary:
                logger.debug(f"MemorySummarizer 生成摘要: {summary[:100]}...")
                return summary

        except Exception as e:
            logger.warning(f"MemorySummarizer 生成摘要失败: {e}")

        # 回退: 简单截断
        return truncated[:self._max_output_length] + "...(truncated)"

    async def summarize_tool_result(
        self,
        content: str,
        tool_name: str,
        function_name: str,
    ) -> str:
        """生成工具结果的摘要

        :param content: 工具返回的原始内容
        :param tool_name: 工具集名称
        :param function_name: 具体工具函数名
        :return: 摘要文本
        """
        context = f"{tool_name}.{function_name}"
        return await self.summarize(content, context)

    async def batch_summarize(
        self,
        messages: list[Dict[str, Any]],
    ) -> None:
        """批量为标记为压缩的消息生成摘要

        遍历消息列表,对 content 为 "(removed)" 或截断后的消息生成 LLM 摘要。
        直接修改传入的 messages 列表（共享引用）。
        """
        for msg in messages:
            content = msg.get("content", "")
            if content in ["(removed)", "(empty)"] or content.endswith("...(truncated)"):
                role = msg.get("role", "")
                func_name = msg.get("function_name", "")

                context = None
                if role == "tool" and func_name:
                    context = func_name
                elif role == "assistant":
                    context = "assistant_reply"

                try:
                    summary = await self.summarize(content, context)
                    msg["content"] = summary
                except Exception as e:
                    logger.warning(f"批量摘要失败: {e}")
