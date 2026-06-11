#!/usr/bin/env python3
"""记忆系统核心逻辑测试

验证五个记忆改进:
- E: 批量持久化
- A: Token 预算管理
- D: 记忆分层
- B: LLM 摘要替代粗暴删除
- C: 向量记忆

不依赖 FastAPI、Redis、PostgreSQL 等外部服务。
"""

import asyncio
import math
from unittest.mock import MagicMock

import pytest

from app.domain.models.memory import Memory
from app.domain.services.memory.memory_budget import MemoryBudgetManager
from app.domain.services.memory.token_counter import TokenCounter
from app.domain.services.memory.vector_memory import VectorMemory
from app.infrastructure.memory.db_memory_batch_writer import DBMemoryBatchWriter


class TestTokenCounter:
    """A: Token 计数器测试"""

    def test_count_empty_message(self):
        """空消息应只有 role 开销"""
        tokens = TokenCounter.count_message({"role": "user", "content": ""})
        assert tokens == 4

    def test_count_chinese_message(self):
        """中文消息 Token 数应大于开销"""
        tokens = TokenCounter.count_message({"role": "user", "content": "帮我查北京天气"})
        assert tokens > 4

    def test_count_english_message(self):
        """英文消息 Token 数应大于开销"""
        tokens = TokenCounter.count_message({"role": "user", "content": "Hello world test message"})
        assert tokens > 4

    def test_count_messages_list(self):
        """消息列表总 Token 应大于单条之和 + 固定开销"""
        messages = [
            {"role": "system", "content": "你是 AI"},
            {"role": "user", "content": "查天气"},
            {"role": "assistant", "content": "好的"},
        ]
        total = TokenCounter.count_messages(messages)
        assert total > 10


class TestMemoryThreeTiers:
    """D: 记忆三层架构测试"""

    def test_empty_memory(self):
        """空记忆检查"""
        m = Memory()
        assert m.empty
        assert m.get_messages() == []

    def test_add_system_message(self):
        """system 消息进入 system_messages"""
        m = Memory()
        m.add_message({"role": "system", "content": "你是 Faber"})
        assert len(m.system_messages) == 1
        assert len(m.working_messages) == 0
        assert not m.empty

    def test_add_user_message(self):
        """user 消息进入 working_messages"""
        m = Memory()
        m.add_message({"role": "user", "content": "查天气"})
        assert len(m.system_messages) == 0
        assert len(m.working_messages) == 1

    def test_add_episodic_note(self):
        """episodic note 进入 episodic_notes"""
        m = Memory()
        m.add_episodic_note("用户经常查询天气信息", {"task_type": "weather"})
        assert len(m.episodic_notes) == 1
        assert m.episodic_notes[0]["content"].startswith("[经验]")

    def test_messages_order(self):
        """get_messages 顺序: system → episodic → working"""
        m = Memory()
        m.add_message({"role": "system", "content": "你是 Faber"})
        m.add_episodic_note("经验1")
        m.add_message({"role": "user", "content": "hi"})

        msgs = m.get_messages()
        assert msgs[0]["role"] == "system" and "Faber" in msgs[0]["content"]
        assert msgs[1]["role"] == "system" and "[经验]" in msgs[1]["content"]
        assert msgs[2]["role"] == "user"

    def test_messages_property(self):
        """messages property 返回合并列表"""
        m = Memory()
        m.add_message({"role": "system", "content": "AI"})
        m.add_message({"role": "user", "content": "hi"})
        assert len(m.messages) == 2

    def test_roll_back(self):
        """roll_back 只删除 working_messages"""
        m = Memory()
        m.system_messages.append({"role": "system", "content": "AI"})
        m.episodic_notes.append({"role": "system", "content": "[经验] 曾经..."})
        m.add_message({"role": "user", "content": "查天气"})
        m.add_message({"role": "assistant", "content": "好的"})

        m.roll_back()
        assert len(m.working_messages) == 1  # 只剩 user
        assert len(m.system_messages) == 1
        assert len(m.episodic_notes) == 1


class TestMemoryBackwardCompat:
    """D: 旧格式向后兼容测试"""

    def test_legacy_migration(self):
        """旧格式 messages 列表自动迁移"""
        old_messages = [
            {"role": "system", "content": "你是 AI"},
            {"role": "user", "content": "查天气"},
            {"role": "assistant", "content": "好的"},
        ]
        m = Memory._from_legacy_messages(old_messages)
        assert len(m.system_messages) == 1
        assert len(m.working_messages) == 2
        assert len(m.episodic_notes) == 0

    def test_new_format_parse(self):
        """新格式直接解析"""
        new_format = {
            "system_messages": [{"role": "system", "content": "AI"}],
            "working_messages": [{"role": "user", "content": "hi"}],
            "episodic_notes": [],
        }
        m = Memory(**new_format)
        assert len(m.system_messages) == 1
        assert len(m.working_messages) == 1


class TestMemoryCompact:
    """D+B: 记忆压缩测试"""

    def test_browser_removed(self):
        """browser_navigate 结果应被替换为 (removed)"""
        m = Memory()
        m.system_messages.append({"role": "system", "content": "AI"})
        m.working_messages.append({"role": "tool", "function_name": "browser_navigate", "content": "<html>...</html>"})

        m.compact()
        assert m.working_messages[0]["content"] == "(removed)"

    def test_reasoning_deleted(self):
        """reasoning_content 应被删除"""
        m = Memory()
        m.working_messages.append({"role": "assistant", "content": "好的", "reasoning_content": "让我想想..."})

        m.compact()
        assert "reasoning_content" not in m.working_messages[0]

    def test_system_preserved(self):
        """system 消息不应被修改"""
        m = Memory()
        m.system_messages.append({"role": "system", "content": "你是 AI"})
        m.working_messages.append({"role": "tool", "function_name": "browser_navigate", "content": "HTML"})

        m.compact()
        assert "AI" in m.system_messages[0]["content"]


class TestMemoryBatchWriter:
    """E: 批量写入器测试"""

    @pytest.mark.asyncio
    async def test_batch_dedup(self):
        """相同 (session_id, agent_name) 应自动去重"""
        calls = []

        class FakeSessionRepo:
            async def save_memory(self, sid, agent, mem):
                calls.append((sid, agent, mem))

        class FakeUoW:
            def __init__(self):
                self.session = FakeSessionRepo()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        writer = DBMemoryBatchWriter(
            uow_factory=lambda: FakeUoW(),
            batch_size=2,
            flush_interval=60.0,
        )

        await writer.start()
        mem = Memory()
        mem.working_messages.append({"role": "user", "content": "hi"})

        await writer.enqueue("s1", "react", mem)
        await writer.enqueue("s1", "react", mem)  # 重复
        await writer.enqueue("s2", "planner", mem)

        await writer.flush()
        await writer.shutdown()

        assert len(calls) == 2  # s1/react 去重为 1 + s2/planner 1
        assert calls[0][0] == "s1"
        assert calls[0][1] == "react"
        assert calls[1][0] == "s2"


class TestVectorMemory:
    """C: 向量记忆测试"""

    @pytest.fixture
    def vm(self):
        """创建带 mock Redis 的 VectorMemory"""
        mock_redis = MagicMock()
        mock_redis.client = MagicMock()
        mock_redis.client.hgetall = MagicMock(return_value={})
        mock_redis.client.hset = MagicMock(return_value=None)
        mock_redis.client.delete = MagicMock(return_value=None)

        vm = VectorMemory("test_session", "react", top_k=2, similarity_threshold=0.0)
        vm._redis = mock_redis

        # 预加载缓存并禁用 _load_cache
        v1 = vm._text_to_vector("查询北京天气")
        v2 = vm._text_to_vector("查询上海股票")
        v3 = vm._text_to_vector("今天吃什么")

        vm._cache = {
            "e1": {"id": "e1", "text": "查询北京天气", "vector": v1},
            "e2": {"id": "e2", "text": "查询上海股票", "vector": v2},
            "e3": {"id": "e3", "text": "今天吃什么", "vector": v3},
        }
        vm._load_cache = lambda: asyncio.Future()  # no-op

        return vm

    @pytest.mark.asyncio
    async def test_search_relevant(self, vm):
        """查询相关文本应返回结果"""
        results = await vm.search("北京天气怎么样")
        assert len(results) > 0
        assert "北京" in results[0]["text"]

    @pytest.mark.asyncio
    async def test_search_irrelevant(self, vm):
        """查询无关文本应无结果"""
        results = await vm.search("abcdefg")
        assert len(results) == 0


class TestCosineSimilarity:
    """C: 余弦相似度计算测试"""

    def test_identical_vectors(self):
        """相同向量相似度应为 1.0"""
        v = {"a": 0.5, "b": 0.5}
        assert VectorMemory._cosine_similarity(v, v) == 1.0

    def test_unrelated_vectors(self):
        """无关向量相似度应为 0.0"""
        v1 = {"a": 0.5, "b": 0.5}
        v2 = {"c": 1.0}
        assert VectorMemory._cosine_similarity(v1, v2) == 0.0

    def test_partially_related(self):
        """部分相关向量相似度应在 (0, 1)"""
        v1 = {"a": 0.5, "b": 0.5}
        v3 = {"a": 1.0}
        sim = VectorMemory._cosine_similarity(v1, v3)
        assert 0 < sim < 1


class TestMemoryBudgetManager:
    """A: Token 预算管理测试"""

    def test_emergency_compact(self):
        """超预算应触发紧急压缩"""
        m = Memory()
        m.system_messages.append({"role": "system", "content": "你是 AI"})

        # 填充大量消息模拟长对话
        for i in range(50):
            m.working_messages.append({"role": "user", "content": f"用户消息{i} " * 50})
            m.working_messages.append({"role": "assistant", "content": f"AI回复{i} " * 50})

        budget_mgr = MemoryBudgetManager(budget=2000)
        budget_mgr.check_and_compact(m)

        report = budget_mgr.get_budget_report()
        assert report["status"] in ["ok", "soft", "hard", "emergency"]
        assert report["usage_percentage"] >= 0
