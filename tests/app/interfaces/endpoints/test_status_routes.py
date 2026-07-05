#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
状态模块接口测试（原有测试）
"""


def test_get_status(client, api_url: str):
    """测试获取应用状态 API 接口"""
    # 1.使用客户端请求获取数据
    response = client.get(f"{api_url}/status")
    data = response.json()

    # 2.断言状态码和业务状态码
    assert response.status_code == 200
    assert data.get("code") == 200
