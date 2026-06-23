from typing import List, Protocol


class Embedder(Protocol):
    """Embedding 向量生成接口协议

    独立于 LLM 接口：DeepSeek 等部分模型不提供 embedding，
    因此记忆系统的情景记忆向量生成走这个独立接口。所有兼容
    OpenAI embeddings 接口的 provider 都可实现该协议。
    """

    @property
    def dimension(self) -> int:
        """只读属性，返回向量的维度（需与 pgvector 列维度一致）"""
        ...

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """批量生成文本向量，返回顺序与入参一致"""
        ...

    async def embed_query(self, text: str) -> List[float]:
        """生成单条文本向量（常用于查询向量）"""
        ...
