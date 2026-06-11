import json
import logging
import math
import re
from collections import Counter
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

from app.infrastructure.storage.redis import get_redis

logger = logging.getLogger(__name__)


class VectorMemory:
    """轻量级向量记忆系统

    使用词频向量 + 余弦相似度实现语义检索,
    无需外部 embedding 服务或向量数据库。

    适合中小规模历史记忆的快速相似度检索。
    存储后端为 Redis Hash,每个会话+Agent 独立存储。
    """

    def __init__(self, session_id: str, agent_name: str, top_k: int = 3, similarity_threshold: float = 0.15):
        """构造函数

        :param session_id: 会话 ID
        :param agent_name: Agent 名称 (planner/react)
        :param top_k: 每次检索返回的最相关条目数
        :param similarity_threshold: 相似度阈值,低于此值的结果被过滤
        """
        self._session_id = session_id
        self._agent_name = agent_name
        self._top_k = top_k
        self._similarity_threshold = similarity_threshold
        self._redis_key = f"vector_memory:{session_id}:{agent_name}"
        self._redis = get_redis()
        self._cache: Dict[str, Dict[str, Any]] = {}  # 内存缓存

    async def _load_cache(self) -> None:
        """从 Redis 加载到内存缓存"""
        try:
            data = await self._redis.client.hgetall(self._redis_key)
            self._cache = {k: json.loads(v) for k, v in data.items()}
        except Exception as e:
            logger.warning(f"VectorMemory 加载缓存失败: {e}")
            self._cache = {}

    async def _save_entry(self, entry_id: str, entry: Dict[str, Any]) -> None:
        """保存单条记录到 Redis"""
        try:
            await self._redis.client.hset(self._redis_key, entry_id, json.dumps(entry))
        except Exception as e:
            logger.warning(f"VectorMemory 保存记录失败: {e}")

    def _text_to_vector(self, text: str) -> Dict[str, float]:
        """将文本转换为词频向量

        策略:
        - 中文按字符分词
        - 英文按单词分词
        - 去除停用词(简化版)
        - 归一化词频
        """
        if not text:
            return {}

        # 提取中文字符和英文单词
        chinese_chars = re.findall(r'[一-鿿]', text)
        english_words = re.findall(r'[a-zA-Z]+', text.lower())

        # 简化停用词过滤
        stop_words = {'的', '了', '是', '在', '我', '有', '和', '就', '不', '人', '都', '一', '一个', '上', '也', '很', '到', '说', '要', '去', '你', '会', '着', '没有', '看', '好', '自己', '这', 'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'shall', 'should', 'can', 'could', 'may', 'might', 'must', 'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from', 'as', 'into', 'through', 'during', 'before', 'after', 'above', 'below', 'between', 'under', 'and', 'but', 'or', 'yet', 'so', 'if', 'because', 'although', 'though', 'while', 'where', 'when', 'that', 'which', 'who', 'whom', 'whose', 'what', 'this', 'these', 'those', 'it', 'its'}

        tokens = [c for c in chinese_chars if c not in stop_words]
        tokens += [w for w in english_words if w not in stop_words and len(w) > 1]

        if not tokens:
            return {}

        counter = Counter(tokens)
        total = sum(counter.values())
        return {token: count / total for token, count in counter.items()}

    @staticmethod
    def _cosine_similarity(v1: Dict[str, float], v2: Dict[str, float]) -> float:
        """计算两个词频向量的余弦相似度

        返回值范围: 0.0 ~ 1.0
        """
        if not v1 or not v2:
            return 0.0

        common = set(v1.keys()) & set(v2.keys())
        if not common:
            return 0.0

        dot_product = sum(v1[w] * v2[w] for w in common)
        norm1 = math.sqrt(sum(v ** 2 for v in v1.values()))
        norm2 = math.sqrt(sum(v ** 2 for v in v2.values()))

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return dot_product / (norm1 * norm2)

    async def add(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """添加一条文本到向量记忆

        :param text: 要记忆的文本
        :param metadata: 元数据(如 role, timestamp 等)
        :return: 记忆条目 ID
        """
        await self._load_cache()

        entry_id = f"entry_{datetime.now().timestamp():.6f}"
        vector = self._text_to_vector(text)

        entry = {
            "id": entry_id,
            "text": text,
            "vector": vector,
            "metadata": metadata or {},
            "created_at": datetime.now().isoformat(),
        }

        self._cache[entry_id] = entry
        await self._save_entry(entry_id, entry)

        logger.debug(f"VectorMemory 添加记录: {entry_id}, text={text[:50]}...")
        return entry_id

    async def search(self, query: str) -> List[Dict[str, Any]]:
        """检索与查询最相关的历史记忆

        :param query: 查询文本
        :return: 相关记忆列表(按相似度降序)
        """
        await self._load_cache()

        if not self._cache:
            return []

        query_vector = self._text_to_vector(query)
        if not query_vector:
            return []

        # 计算与所有记忆的相似度
        scored: List[Tuple[str, float]] = []
        for entry_id, entry in self._cache.items():
            vector = entry.get("vector", {})
            similarity = self._cosine_similarity(query_vector, vector)
            if similarity >= self._similarity_threshold:
                scored.append((entry_id, similarity))

        # 按相似度降序排序
        scored.sort(key=lambda x: x[1], reverse=True)

        # 取 top_k
        results = []
        for entry_id, similarity in scored[:self._top_k]:
            entry = self._cache[entry_id]
            results.append({
                "id": entry_id,
                "text": entry["text"],
                "similarity": round(similarity, 4),
                "metadata": entry.get("metadata", {}),
            })

        logger.debug(f"VectorMemory 检索: query={query[:50]}..., 找到 {len(results)} 条相关记忆")
        return results

    async def clear(self) -> None:
        """清空当前会话的向量记忆"""
        try:
            await self._redis.client.delete(self._redis_key)
            self._cache = {}
            logger.info(f"VectorMemory 清空: {self._redis_key}")
        except Exception as e:
            logger.warning(f"VectorMemory 清空失败: {e}")

    async def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        await self._load_cache()
        return {
            "total_entries": len(self._cache),
            "session_id": self._session_id,
            "agent_name": self._agent_name,
            "redis_key": self._redis_key,
        }
