#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
应用配置模块接口测试
"""


class TestAppConfig:
    """应用配置（LLM / Agent / MCP / A2A）接口测试"""

    def test_get_llm_config(self, client, api_url: str):
        """测试 GET /api/app-config/llm"""
        response = client.get(f"{api_url}/app-config/llm")
        assert response.status_code == 200

        data = response.json()
        assert data["code"] == 200
        # response_model 会重新校验并补齐默认值，这里只校验关键字段存在
        assert "model_name" in data["data"]
        assert "base_url" in data["data"]

    def test_update_llm_config(self, client, api_url: str):
        """测试 POST /api/app-config/llm"""
        payload = {
            "base_url": "https://api.deepseek.com",
            "model_name": "deepseek-reasoner",
            "temperature": 0.7,
            "max_tokens": 8192,
        }
        response = client.post(f"{api_url}/app-config/llm", json=payload)
        assert response.status_code == 200

        data = response.json()
        assert data["code"] == 200
        assert data["data"]["model_name"] == "deepseek-reasoner"

    def test_get_agent_config(self, client, api_url: str):
        """测试 GET /api/app-config/agent"""
        response = client.get(f"{api_url}/app-config/agent")
        assert response.status_code == 200

        data = response.json()
        assert data["code"] == 200
        assert "max_iterations" in data["data"]

    def test_update_agent_config(self, client, api_url: str):
        """测试 POST /api/app-config/agent"""
        payload = {
            "max_iterations": 100,
            "max_retries": 3,
            "max_search_results": 10,
        }
        response = client.post(f"{api_url}/app-config/agent", json=payload)
        assert response.status_code == 200

        data = response.json()
        assert data["code"] == 200
        assert data["data"]["max_iterations"] == 100

    def test_get_mcp_servers(self, client, api_url: str):
        """测试 GET /api/app-config/mcp-servers"""
        response = client.get(f"{api_url}/app-config/mcp-servers")
        assert response.status_code == 200

        data = response.json()
        assert data["code"] == 200
        assert "mcp_servers" in data["data"]

    def test_create_update_delete_mcp_server(self, client, api_url: str):
        """测试 MCP 服务的增改删流程"""
        server_name = "test-mcp-server"

        # 新增
        create_payload = {
            "mcpServers": {
                server_name: {
                    "transport": "streamable_http",
                    "enabled": True,
                    "url": "https://example.com/mcp",
                }
            }
        }
        response = client.post(f"{api_url}/app-config/mcp-servers", json=create_payload)
        assert response.status_code == 200
        assert response.json()["code"] == 200

        # 更新启用状态
        response = client.post(
            f"{api_url}/app-config/mcp-servers/{server_name}/enabled",
            json={"enabled": False},
        )
        assert response.status_code == 200
        assert response.json()["code"] == 200

        # 删除
        response = client.post(f"{api_url}/app-config/mcp-servers/{server_name}/delete")
        assert response.status_code == 200
        assert response.json()["code"] == 200

    def test_get_a2a_servers(self, client, api_url: str):
        """测试 GET /api/app-config/a2a-servers"""
        response = client.get(f"{api_url}/app-config/a2a-servers")
        assert response.status_code == 200

        data = response.json()
        assert data["code"] == 200
        assert "a2a_servers" in data["data"]

    def test_create_and_delete_a2a_server(self, client, api_url: str):
        """测试 A2A 服务的新增与删除"""
        # 新增（可能因无法连接而失败，但接口应被调用）
        response = client.post(
            f"{api_url}/app-config/a2a-servers",
            json={"base_url": "https://example.com/a2a"},
        )
        assert response.status_code in (200, 500)

        if response.status_code == 200:
            a2a_id = response.json()["data"].get("id")
            if a2a_id:
                response = client.post(
                    f"{api_url}/app-config/a2a-servers/{a2a_id}/enabled",
                    json={"enabled": False},
                )
                assert response.status_code == 200

                response = client.post(
                    f"{api_url}/app-config/a2a-servers/{a2a_id}/delete"
                )
                assert response.status_code == 200
