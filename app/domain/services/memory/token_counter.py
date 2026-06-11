import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class TokenCounter:
    """Token 计数器

    使用启发式方法估算消息的 Token 数,避免引入 tiktoken 等外部依赖。
    对中文和英文采用不同的估算系数,总体趋向保守(高估而非低估),
    确保不会意外超出模型上下文窗口。
    """

    # 估算系数
    CHINESE_CHAR_TOKEN_RATIO = 1.5   # 中文字符 → Token 比例
    ENGLISH_WORD_TOKEN_RATIO = 1.3   # 英文单词 → Token 比例
    FUNCTION_CALL_TOKEN_ESTIMATE = 20  # 工具调用描述的基础 Token 数

    @classmethod
    def count_message(cls, message: Dict[str, Any]) -> int:
        """估算单条消息的 Token 数

        :param message: OpenAI 格式的消息字典
        :return: 估算的 Token 数
        """
        total = 0

        # 1. role 字段固定开销
        total += 4  # "role": "xxx" 的 JSON 开销

        # 2. content 字段
        content = message.get("content") or ""
        total += cls._estimate_text_tokens(content)

        # 3. tool_calls 字段
        tool_calls = message.get("tool_calls")
        if tool_calls:
            for tool_call in tool_calls:
                total += cls.FUNCTION_CALL_TOKEN_ESTIMATE
                # function name
                func_name = tool_call.get("function", {}).get("name", "")
                total += len(func_name.split()) * cls.ENGLISH_WORD_TOKEN_RATIO
                # arguments
                arguments = tool_call.get("function", {}).get("arguments", "")
                total += cls._estimate_text_tokens(arguments)

        # 4. tool_call_id 和 function_name 字段
        if message.get("role") == "tool":
            total += 4  # tool_call_id 开销
            total += 4  # function_name 开销

        # 5. reasoning_content 字段(DeepSeek 思考过程)
        reasoning = message.get("reasoning_content", "")
        if reasoning:
            total += cls._estimate_text_tokens(reasoning)

        return int(total)

    @classmethod
    def count_messages(cls, messages: List[Dict[str, Any]]) -> int:
        """估算消息列表的总 Token 数

        额外加上 OpenAI API 的消息格式开销:
        - 每条消息有固定的 3 token 开销
        - 整个 messages 数组有固定的 2 token 开销
        """
        if not messages:
            return 0

        total = 2  # 整个数组开销
        for message in messages:
            total += cls.count_message(message)
            total += 3  # 单条消息固定开销

        return int(total)

    @classmethod
    def _estimate_text_tokens(cls, text: str) -> float:
        """估算文本的 Token 数

        策略:
        - 中文字符: 每个约 1.5 token
        - 英文单词: 每个约 1.3 token
        - 数字和标点: 统一按英文处理
        """
        if not text:
            return 0

        chinese_chars = sum(1 for c in text if '一' <= c <= '鿿')
        other_chars = len(text) - chinese_chars

        # 其他字符按空格分词估算单词数
        words = other_chars / 4.5  # 粗略估算: 平均每个英文单词 4.5 字符

        return (
            chinese_chars * cls.CHINESE_CHAR_TOKEN_RATIO +
            words * cls.ENGLISH_WORD_TOKEN_RATIO
        )

    @classmethod
    def format_budget_status(cls, current: int, budget: int) -> str:
        """格式化预算状态字符串,用于日志"""
        percentage = (current / budget * 100) if budget > 0 else 0
        return f"{current}/{budget} ({percentage:.1f}%)"
