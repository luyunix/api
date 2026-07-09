#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试公共fixture
"""
import os
import signal
import subprocess
import sys
import time

# 禁用系统代理，避免 httpx 在测试过程中误走失效的 SOCKS/HTTP 代理
# （macOS 系统代理设置会影响 httpx 的内部请求）
for _proxy_key in (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
):
    os.environ.pop(_proxy_key, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

import pytest
from fastapi.testclient import TestClient
from httpx import Client

from app.main import app


TEST_EMAIL = "pytest-user@faber.local"
TEST_PASSWORD = "pytest-password"


def _auth_headers(client, api_prefix: str) -> dict[str, str]:
    """注册/登录测试用户并返回认证头。"""
    payload = {
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD,
        "username": "Pytest User",
    }
    response = client.post(f"{api_prefix}/auth/register", json=payload)
    if response.status_code != 200:
        response = client.post(
            f"{api_prefix}/auth/login",
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        )
    response.raise_for_status()
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _is_server_ready(url: str, timeout: float = 1.0) -> bool:
    """检查本地服务是否已可访问"""
    try:
        c = Client(base_url=url, timeout=timeout)
        r = c.get("/api/status")
        c.close()
        return r.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="session")
def client() -> TestClient:
    """
    同步 TestClient 客户端，scope="session" 整个测试会话只实例化一次。
    TestClient 会自动管理应用 lifespan（启动/关闭）。
    """
    with TestClient(app) as c:
        c.headers.update(_auth_headers(c, "/api"))
        yield c


@pytest.fixture(scope="session")
def running_server():
    """
    确保本地服务在测试期间可用。
    如果 http://localhost:8000 已在运行则直接使用；
    否则在子进程中启动 uvicorn，测试结束后自动关闭。
    """
    base_url = "http://localhost:8000"

    if _is_server_ready(base_url):
        yield base_url
        return

    # 准备启动命令：使用当前 Python 解释器对应的 uvicorn
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    venv_python = sys.executable
    # 优先使用项目虚拟环境中的 uvicorn
    venv_uvicorn = os.path.join(project_root, ".venv", "bin", "uvicorn")
    if os.path.exists(venv_uvicorn):
        cmd = [venv_uvicorn]
    else:
        cmd = [venv_python, "-m", "uvicorn"]

    cmd.extend([
        "app.main:app",
        "--host", "0.0.0.0",
        "--port", "8000",
        "--timeout-graceful-shutdown", "5",
    ])

    # 继承当前环境，并强制禁用代理
    env = os.environ.copy()
    env.update({
        "HTTP_PROXY": "",
        "HTTPS_PROXY": "",
        "ALL_PROXY": "",
        "http_proxy": "",
        "https_proxy": "",
        "all_proxy": "",
        "NO_PROXY": "*",
        "no_proxy": "*",
    })

    process = subprocess.Popen(
        cmd,
        cwd=project_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # 等待服务就绪，最多 60 秒
    deadline = time.time() + 60
    ready = False
    while time.time() < deadline:
        if process.poll() is not None:
            break
        if _is_server_ready(base_url, timeout=2.0):
            ready = True
            break
        time.sleep(1)

    if not ready:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        stdout, stderr = process.communicate(timeout=5)
        raise RuntimeError(
            f"无法启动本地服务 {base_url}\n"
            f"STDOUT: {stdout.decode('utf-8', errors='ignore')}\n"
            f"STDERR: {stderr.decode('utf-8', errors='ignore')}"
        )

    try:
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture(scope="session")
def live_client(running_server) -> Client:
    """
    连接到本地真实服务的同步 HTTP 客户端。
    用于 TestClient 无法处理的无限 SSE 流、WebSocket 等场景。
    如果服务未运行，running_server fixture 会自动启动它。
    """
    c = Client(base_url=running_server, timeout=5)
    c.headers.update(_auth_headers(c, "/api"))
    try:
        yield c
    finally:
        c.close()


@pytest.fixture(scope="session")
def base_url() -> str:
    """测试基础 URL"""
    return "http://testserver"


@pytest.fixture(scope="session")
def api_url(base_url: str) -> str:
    """API 前缀 URL"""
    return f"{base_url}/api"


@pytest.fixture(scope="session")
def live_api_url(running_server) -> str:
    """真实服务的 API 前缀 URL"""
    return f"{running_server}/api"
