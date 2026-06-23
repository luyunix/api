import logging
from typing import Optional, List, Callable

import json_repair

from app.domain.external.embedder import Embedder
from app.domain.external.llm import LLM
from app.domain.models.episodic_memory import EpisodicMemory
from app.domain.models.plan import Plan
from app.domain.models.message import Message
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)


class EpisodicMemoryService:
    """情景记忆服务：跨会话经验的学习闭环

    - retrieve_relevant：任务开始时，把用户查询 embedding 后从 pgvector 召回相关经验，
      格式化为经验笔记供 Agent 注入记忆。
    - index_task：任务完成时，用 LLM 从完成的任务中提炼可复用经验，embedding 后写入 pgvector。

    当未配置 Embedder（embedding_config.enabled=false）时，本服务整体降级为空操作，
    不影响普通会话功能。
    """

    def __init__(
            self,
            embedder: Optional[Embedder],
            uow_factory: Callable[[], IUnitOfWork],
            llm: LLM,
            top_k: int = 3,
            max_distance: float = 0.6,
    ) -> None:
        """构造函数

        :param embedder: Embedding 生成器（None 表示情景记忆未启用）
        :param uow_factory: UoW 工厂，用于在事务内访问 episodic_memory 仓库
        :param llm: 经验提炼用的语言模型
        :param top_k: 每次召回返回的最大条数
        :param max_distance: 余弦距离阈值（越小越相似，0=完全相同，2=相反）
        """
        self._embedder = embedder
        self._uow_factory = uow_factory
        self._llm = llm
        self._top_k = top_k
        self._max_distance = max_distance

    @property
    def enabled(self) -> bool:
        """情景记忆是否启用"""
        return self._embedder is not None

    # ------------------------------------------------------------------ #
    # 召回
    # ------------------------------------------------------------------ #
    async def retrieve_relevant(self, query: str, agent_name: str) -> List[str]:
        """召回与查询相关的历史经验，返回经验笔记文本列表"""
        if not self._embedder or not query or not query.strip():
            return []

        try:
            query_vec = await self._embedder.embed_query(query)
            async with self._uow_factory() as uow:
                hits = await uow.episodic_memory.search(
                    query_vec, agent_name, self._top_k, self._max_distance,
                )
                # 更新使用计数与最近召回时间
                for hit in hits:
                    await uow.episodic_memory.increment_use(hit.id)

            if hits:
                logger.info(f"EpisodicMemory 召回 {len(hits)} 条相关经验 (agent={agent_name})")
            return [self._format_note(hit) for hit in hits]
        except Exception as e:
            logger.warning(f"EpisodicMemory 召回经验失败 (agent={agent_name}): {e}")
            return []

    def _format_note(self, memory: EpisodicMemory) -> str:
        """将一条情景记忆格式化为注入记忆的经验笔记"""
        excerpt = memory.content[:200] + "..." if len(memory.content) > 200 else memory.content
        note = f"曾经处理过类似任务「{memory.summary}」: {excerpt}"
        tags = memory.metadata.get("tags") if isinstance(memory.metadata, dict) else None
        if tags:
            note += f" [关键词: {', '.join(tags)}]"
        return note

    # ------------------------------------------------------------------ #
    # 写入（学习）
    # ------------------------------------------------------------------ #
    async def index_task(self, session_id: str, agent_name: str, plan: Plan, message: Message) -> None:
        """任务完成后提炼可复用经验并写入 pgvector"""
        if not self._embedder:
            return

        try:
            # 1.LLM 提炼经验
            lessons = await self._extract_lessons(plan, message)
            if not lessons:
                logger.info(f"EpisodicMemory 未从任务中提炼出可复用经验 (session={session_id})")
                return

            # 2.批量 embedding
            texts = [lesson["content"] for lesson in lessons]
            vectors = await self._embedder.embed(texts)

            # 3.写入 pgvector
            async with self._uow_factory() as uow:
                for lesson, vec in zip(lessons, vectors):
                    record = EpisodicMemory(
                        agent_name=agent_name,
                        source_session=session_id,
                        summary=lesson.get("summary", ""),
                        content=lesson["content"],
                        metadata={
                            "goal": plan.goal,
                            "tags": lesson.get("tags", []),
                            **(lesson.get("metadata") or {}),
                        },
                        importance=float(lesson.get("importance", 0.5)),
                        embedding=vec,
                    )
                    await uow.episodic_memory.add(record)

            logger.info(
                f"EpisodicMemory 写入 {len(lessons)} 条经验 (session={session_id}, agent={agent_name})"
            )
        except Exception as e:
            logger.warning(f"EpisodicMemory 写入经验失败 (session={session_id}): {e}")

    async def _extract_lessons(self, plan: Plan, message: Message) -> List[dict]:
        """调用 LLM 从完成的任务中提炼可复用经验

        返回 [{summary, content, importance, tags, metadata?}]，失败返回 []。
        """
        # 1.组装任务执行摘要（步骤描述 + 结果）
        steps_summary = "\n".join(
            f"- {s.description}：{(s.result or '无结果')[:120]}"
            for s in plan.steps
        ) or "无"

        prompt = f"""任务已结束，请从以下任务记录中提炼「可复用的经验」。

任务目标：{plan.goal}
用户原始需求：{message.message[:300]}
执行步骤与结果：
{steps_summary}

提炼要求：
- 每条经验是一个独立、可迁移的结论（不是流水账）
- 覆盖：适用场景 / 有效做法 / 常见陷阱
- 只产出真正可复用的经验，宁缺毋滥；如果没有值得沉淀的，返回空数组
- 使用中文

返回格式：必须是符合以下 TypeScript 接口的 JSON：
```typescript
interface LessonsResponse {{
  lessons: Array<{{
    summary: string;       // 一句话标题
    content: string;       // 完整经验文本（适用场景+做法+陷阱）
    importance: number;    // 0~1，影响召回权重
    tags: string[];        // 关键词标签
  }}>;
}}
```

JSON 输出示例：
{{
  "lessons": [
    {{
      "summary": "用搜索工具查实时信息",
      "content": "当需要获取最新价格/参数时，优先用 search_web 而非内部知识；搜索后用浏览器访问官网核实关键数据。",
      "importance": 0.7,
      "tags": ["搜索", "事实核查"]
    }}
  ]
}}"""

        try:
            response = await self._llm.invoke(
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                tools=None,
            )
            content = response.get("content", "") if isinstance(response, dict) else ""
            parsed = json_repair.loads(content) if content else {}
            lessons = parsed.get("lessons", []) if isinstance(parsed, dict) else []
            # 基本校验
            return [l for l in lessons if isinstance(l, dict) and l.get("content")]
        except Exception as e:
            logger.warning(f"EpisodicMemory 提炼经验失败: {e}")
            return []
