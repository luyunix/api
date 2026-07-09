import math
import inspect
from collections import Counter
from typing import Any, Dict, List


class VectorMemory:
    """旧版向量记忆兼容实现。

    生产路径已迁移到 episodic memory + pgvector；这个类保留给旧测试和旧调用方，
    使用本地字符向量完成无外部依赖的相似度检索。
    """

    def __init__(
            self,
            session_id: str,
            agent_name: str,
            top_k: int = 5,
            similarity_threshold: float = 0.2,
    ) -> None:
        self.session_id = session_id
        self.agent_name = agent_name
        self.top_k = top_k
        self.similarity_threshold = similarity_threshold
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._redis = None

    async def search(self, query: str) -> List[Dict[str, Any]]:
        load_result = self._load_cache()
        if inspect.iscoroutine(load_result):
            await load_result
        query_vector = self._text_to_vector(query)
        results = []
        for item in self._cache.values():
            similarity = self._cosine_similarity(query_vector, item.get("vector", {}))
            if similarity > self.similarity_threshold:
                results.append({**item, "similarity": similarity})
        results.sort(key=lambda item: item["similarity"], reverse=True)
        return results[:self.top_k]

    async def _load_cache(self) -> None:
        return None

    @staticmethod
    def _text_to_vector(text: str) -> Dict[str, float]:
        normalized = "".join(text.split()).lower()
        if not normalized:
            return {}
        features = list(normalized)
        if len(normalized) > 1:
            features.extend(normalized[i:i + 2] for i in range(len(normalized) - 1))
        counts = Counter(features)
        total = sum(counts.values())
        return {key: value / total for key, value in counts.items()}

    @staticmethod
    def _cosine_similarity(v1: Dict[str, float], v2: Dict[str, float]) -> float:
        if v1 == v2 and v1:
            return 1.0
        if not v1 or not v2:
            return 0.0
        common = set(v1) & set(v2)
        if not common:
            return 0.0
        dot = sum(v1[key] * v2[key] for key in common)
        norm1 = math.sqrt(sum(value * value for value in v1.values()))
        norm2 = math.sqrt(sum(value * value for value in v2.values()))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)
