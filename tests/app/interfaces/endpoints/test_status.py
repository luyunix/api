#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
状态模块接口测试
"""


class TestStatus:
    """系统健康检查相关接口测试"""

    def test_get_status(self, client, api_url: str):
        """测试 GET /api/status 健康检查接口"""
        response = client.get(f"{api_url}/status")
        assert response.status_code == 200

        data = response.json()
        assert data["code"] == 200
        assert data["msg"] == "系统健康检查成功"
        assert isinstance(data["data"], list)

        # 至少包含 postgres 和 redis 检查项
        services = [item["service"] for item in data["data"]]
        assert "postgres" in services
        assert "redis" in services
        for item in data["data"]:
            assert item["status"] == "ok"
