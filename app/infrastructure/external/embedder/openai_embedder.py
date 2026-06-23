import logging
from typing import List

from openai import AsyncOpenAI

from app.domain.external.embedder import Embedder
from app.domain.models.app_config import EmbeddingConfig

logger = logging.getLogger(__name__)


class OpenAIEmbedder(Embedder):
    """基于 OpenAI 兼容接口的 Embedding 生成类

    适用于所有兼容 OpenAI embeddings 接口的 provider：
    - DashScope (Qwen text-embedding-v3)
    - SiliconFlow (BAAI/bge-m3)
    - OpenAI (text-embedding-3-small)
    - 本地服务（Ollama / vLLM 等）
    """

    def __init__(self, config: EmbeddingConfig) -> None:
        """构造函数，完成异步 OpenAI 客户端与参数初始化"""
        self._client = AsyncOpenAI(
            base_url=str(config.base_url),
            api_key=config.api_key,
        )
        self._model = config.model_name
        self._dimension = config.dimension
        self._batch_size = config.batch_size

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """批量生成向量，按 batch_size 分批调用接口

        多批结果按原顺序拼接返回。响应 data 按 index 排序以防乱序。
        """
        if not texts:
            return []

        results: List[List[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i:i + self._batch_size]
            try:
                response = await self._client.embeddings.create(
                    model=self._model,
                    input=batch,
                )
                # 按 index 排序，确保结果顺序与入参一致
                batch_vectors = [
                    item.embedding
                    for item in sorted(response.data, key=lambda x: x.index)
                ]
                results.extend(batch_vectors)
            except Exception as e:
                logger.error(f"调用Embedding接口生成向量失败(batch[{i}:{i + len(batch)}]): {str(e)}")
                raise

        return results

    async def embed_query(self, text: str) -> List[float]:
        """生成单条查询文本的向量"""
        vectors = await self.embed([text])
        return vectors[0]
