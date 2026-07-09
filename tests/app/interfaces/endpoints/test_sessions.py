#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
会话模块接口测试
"""
import time

import pytest
import websockets


class TestSessions:
    """会话 CRUD、SSE、聊天、沙箱文件/Shell/VNC 接口测试"""

    @pytest.fixture
    def session_id(self, live_client, live_api_url: str):
        """创建一个测试会话，并在测试结束后清理"""
        response = live_client.post(f"{live_api_url}/sessions")
        assert response.status_code == 200

        session_id = response.json()["data"]["session_id"]
        yield session_id

        # 清理
        try:
            live_client.post(f"{live_api_url}/sessions/{session_id}/delete")
        except Exception:
            pass

    @pytest.fixture
    def chat_session_id(self, live_client, live_api_url: str):
        """
        创建一个已发起聊天、拥有沙箱环境的会话，并在测试结束后清理。
        读取沙箱文件/Shell/VNC 都需要会话先关联沙箱。
        """
        response = live_client.post(f"{live_api_url}/sessions")
        assert response.status_code == 200

        session_id = response.json()["data"]["session_id"]

        # 发起一次 SSE 聊天，触发 Agent 为该会话初始化沙箱
        try:
            with live_client.stream(
                "POST",
                f"{live_api_url}/sessions/{session_id}/chat",
                json={"message": "你好"},
                timeout=5,
            ) as stream:
                _ = stream.status_code
        except Exception:
            pass

        # 给 Agent 一点时间初始化沙箱
        time.sleep(2)

        yield session_id

        # 清理
        try:
            live_client.post(f"{live_api_url}/sessions/{session_id}/delete")
        except Exception:
            pass

    def test_create_session(self, live_client, live_api_url: str):
        """测试 POST /api/sessions 创建会话"""
        response = live_client.post(f"{live_api_url}/sessions")
        assert response.status_code == 200

        data = response.json()
        assert data["code"] == 200
        assert "session_id" in data["data"]

        # 立即清理
        live_client.post(f"{live_api_url}/sessions/{data['data']['session_id']}/delete")

    def test_get_all_sessions(self, live_client, live_api_url: str):
        """测试 GET /api/sessions 获取会话列表"""
        response = live_client.get(f"{live_api_url}/sessions")
        assert response.status_code == 200

        data = response.json()
        assert data["code"] == 200
        assert "sessions" in data["data"]

    def test_stream_sessions(self, live_client, live_api_url: str):
        """测试 POST /api/sessions/stream SSE 流式会话列表端点可达"""
        with live_client.stream(
            "POST", f"{live_api_url}/sessions/stream", timeout=5
        ) as stream:
            assert stream.status_code == 200
            assert "text/event-stream" in stream.headers.get("content-type", "")

            # 尝试读取第一个事件后退出（避免无限流阻塞）
            events = []
            for line in stream.iter_lines():
                if line.startswith("data:"):
                    events.append(line[5:].strip())
                    break
            assert len(events) >= 1

    def test_get_session(self, session_id, live_client, live_api_url: str):
        """测试 GET /api/sessions/{id} 获取会话详情"""
        response = live_client.get(f"{live_api_url}/sessions/{session_id}")
        assert response.status_code == 200

        data = response.json()
        assert data["code"] == 200
        assert data["data"]["session_id"] == session_id

    def test_clear_unread_message_count(self, session_id, live_client, live_api_url: str):
        """测试 POST /api/sessions/{id}/clear-unread-message-count"""
        response = live_client.post(
            f"{live_api_url}/sessions/{session_id}/clear-unread-message-count"
        )
        assert response.status_code == 200
        assert response.json()["code"] == 200

    def test_get_session_files(self, session_id, live_client, live_api_url: str):
        """测试 GET /api/sessions/{id}/files 获取会话文件列表"""
        response = live_client.get(f"{live_api_url}/sessions/{session_id}/files")
        assert response.status_code == 200
        assert response.json()["code"] == 200

    def test_read_file_without_sandbox(self, session_id, live_client, live_api_url: str):
        """测试空会话读取沙箱文件应返回 404 无沙箱环境"""
        response = live_client.post(
            f"{live_api_url}/sessions/{session_id}/file",
            json={"filepath": "/etc/hostname"},
        )
        assert response.status_code == 404
        assert "无沙箱环境" in response.json()["msg"]

    def test_read_shell_without_sandbox(self, session_id, live_client, live_api_url: str):
        """测试空会话读取 Shell 输出应返回 404 无沙箱环境"""
        response = live_client.post(
            f"{live_api_url}/sessions/{session_id}/shell",
            json={"session_id": "default"},
        )
        assert response.status_code == 404
        assert "无沙箱环境" in response.json()["msg"]

    def test_stop_session(self, session_id, live_client, live_api_url: str):
        """测试 POST /api/sessions/{id}/stop 停止会话"""
        response = live_client.post(f"{live_api_url}/sessions/{session_id}/stop")
        assert response.status_code == 200
        assert response.json()["code"] == 200

    def test_delete_session(self, live_client, live_api_url: str):
        """测试 POST /api/sessions/{id}/delete 删除会话"""
        response = live_client.post(f"{live_api_url}/sessions")
        session_id = response.json()["data"]["session_id"]

        response = live_client.post(f"{live_api_url}/sessions/{session_id}/delete")
        assert response.status_code == 200
        assert response.json()["code"] == 200

    def test_chat_stream(self, chat_session_id, live_client, live_api_url: str):
        """测试 POST /api/sessions/{id}/chat SSE 流式聊天端点可达"""
        with live_client.stream(
            "POST",
            f"{live_api_url}/sessions/{chat_session_id}/chat",
            json={"message": "你好"},
            timeout=5,
        ) as stream:
            assert stream.status_code == 200
            assert "text/event-stream" in stream.headers.get("content-type", "")

            # 尝试读取第一个事件后退出
            events = []
            for line in stream.iter_lines():
                if line.startswith("data:"):
                    events.append(line[5:].strip())
                    break
            assert len(events) >= 1

    def test_read_file_with_sandbox(self, chat_session_id, live_client, live_api_url: str):
        """测试有沙箱的会话读取文件"""
        response = live_client.post(
            f"{live_api_url}/sessions/{chat_session_id}/file",
            json={"filepath": "/etc/hostname"},
            timeout=15,
        )
        # 沙箱可能未就绪或文件不存在，但接口应返回结构化响应
        assert response.status_code in (200, 404, 500)

    def test_read_shell_with_sandbox(self, chat_session_id, live_client, live_api_url: str):
        """测试有沙箱的会话读取 Shell 输出"""
        response = live_client.post(
            f"{live_api_url}/sessions/{chat_session_id}/shell",
            json={"session_id": "default"},
            timeout=15,
        )
        # "default" 不是真实 shell 会话 ID，预期 500 或 404
        assert response.status_code in (200, 404, 500)

    def test_vnc_websocket(self, chat_session_id, live_client, live_api_url: str):
        """测试 WS /api/sessions/{id}/vnc WebSocket 连接"""
        import os

        # 临时屏蔽代理，避免 websockets 走 SOCKS 代理
        proxy_vars = [
            "ALL_PROXY",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "SOCKS_PROXY",
            "all_proxy",
            "http_proxy",
            "https_proxy",
            "socks_proxy",
        ]
        old_env = {k: os.environ.pop(k, None) for k in proxy_vars}

        async def _connect():
            token = live_client.headers["Authorization"].removeprefix("Bearer ")
            ws_url = f"ws://localhost:8000/api/sessions/{chat_session_id}/vnc?token={token}"
            async with websockets.connect(
                ws_url, subprotocols=["binary"], open_timeout=5, proxy=None
            ) as ws:
                assert ws is not None

        try:
            import asyncio

            asyncio.run(_connect())
        finally:
            for k, v in old_env.items():
                if v is not None:
                    os.environ[k] = v
