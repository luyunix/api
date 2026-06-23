import logging
from typing import Dict, Any, List, Optional

import tiktoken

logger = logging.getLogger(__name__)


class TokenCounter:
    """Token 计数器（基于 tiktoken 精确计数）

    替代旧的启发式估算（中文×1.5 / 英文×1.3）。tiktoken 给出与真实
    分词基本一致的 token 数，让记忆预算/压缩阈值判断更准确，避免旧实现
    因高估而过早触发压缩。

    编码选择：
    - DeepSeek 系列（deepseek-*）使用 o200k_base（与 GPT-4o 同编码）
    - Qwen 系列使用 cl100k_base
    - 其它模型优先走 tiktoken 官方模型→编码映射
    - 全部失败时回退 cl100k_base

    向后兼容：count_message / count_messages 的 model_name 参数可选，
    旧调用（不传 model_name）会回退到 cl100k_base。
    """

    # 模型前缀 → tiktoken 编码名
    _MODEL_PREFIX_ENCODING: Dict[str, str] = {
        "deepseek": "o200k_base",
        "qwen": "cl100k_base",
    }

    # 编码器缓存：cache_key(model_name 或 "default") → Encoding
    _encoders: Dict[str, tiktoken.Encoding] = {}

    @classmethod
    def _get_encoding(cls, model_name: Optional[str] = None) -> tiktoken.Encoding:
        """根据模型名获取（缓存的）tiktoken 编码器"""
        # 1.命中缓存
        cache_key = model_name or "default"
        if cache_key in cls._encoders:
            return cls._encoders[cache_key]

        # 2.根据模型前缀选择编码
        encoding: Optional[tiktoken.Encoding] = None
        if model_name:
            lower = model_name.lower()
            for prefix, enc_name in cls._MODEL_PREFIX_ENCODING.items():
                if lower.startswith(prefix):
                    try:
                        encoding = tiktoken.get_encoding(enc_name)
                        break
                    except Exception as e:
                        logger.debug(f"获取编码[{enc_name}]失败: {e}")
                        continue
            # 3.走 tiktoken 官方模型→编码映射（OpenAI 系列）
            if encoding is None:
                try:
                    encoding = tiktoken.encoding_for_model(model_name)
                except Exception:
                    encoding = None

        # 4.兜底 cl100k_base
        if encoding is None:
            encoding = tiktoken.get_encoding("cl100k_base")

        cls._encoders[cache_key] = encoding
        return encoding

    @classmethod
    def count_text(cls, text: str, model_name: Optional[str] = None) -> int:
        """精确计算文本的 token 数"""
        if not text:
            return 0
        encoding = cls._get_encoding(model_name)
        return len(encoding.encode(text))

    @classmethod
    def count_message(cls, message: Dict[str, Any], model_name: Optional[str] = None) -> int:
        """精确计算单条消息的 token 数（含 tool_calls / reasoning_content）"""
        total = 4  # role + JSON 结构固定开销

        # 1.content 字段
        content = message.get("content") or ""
        total += cls.count_text(content, model_name)

        # 2.tool_calls 字段
        tool_calls = message.get("tool_calls")
        if tool_calls:
            for tool_call in tool_calls:
                func = tool_call.get("function", {})
                total += cls.count_text(func.get("name", ""), model_name)
                total += cls.count_text(func.get("arguments", ""), model_name)
                total += 4  # tool_call id + 结构开销

        # 3.tool 角色额外字段开销
        if message.get("role") == "tool":
            total += 4  # tool_call_id / function_name 开销

        # 4.reasoning_content 字段（DeepSeek 思考过程）
        reasoning = message.get("reasoning_content")
        if reasoning:
            total += cls.count_text(reasoning, model_name)

        return int(total)

    @classmethod
    def count_messages(cls, messages: List[Dict[str, Any]], model_name: Optional[str] = None) -> int:
        """计算消息列表的总 token 数（含 OpenAI 消息格式开销）"""
        if not messages:
            return 0

        total = 2  # 整个数组开销
        for message in messages:
            total += cls.count_message(message, model_name)
            total += 3  # 单条消息固定开销

        return int(total)

    @classmethod
    def format_budget_status(cls, current: int, budget: int) -> str:
        """格式化预算状态字符串，用于日志"""
        percentage = (current / budget * 100) if budget > 0 else 0
        return f"{current}/{budget} ({percentage:.1f}%)"
