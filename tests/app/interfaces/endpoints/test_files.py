#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
文件模块接口测试
"""
import tempfile
from pathlib import Path

import pytest

from app.infrastructure.storage.cos import get_cos


class TestFiles:
    """文件上传/下载/信息查询接口测试"""

    @pytest.fixture(autouse=True)
    def skip_if_cos_not_configured(self):
        """
        如果本地未完整配置腾讯云 COS（region/secret_id/secret_key/bucket），
        则跳过文件相关测试，避免上传时因 bucket 为空抛出异常。
        """
        cos = get_cos()
        settings = cos._settings
        missing = [
            field
            for field in ("cos_region", "cos_secret_id", "cos_secret_key", "cos_bucket")
            if not getattr(settings, field, None)
        ]
        if cos._client is None or missing:
            pytest.skip(
                f"腾讯云 COS 配置不完整（缺少: {', '.join(missing) or 'client 未初始化'}），"
                "跳过文件接口测试"
            )

    def _upload_file(self, client, api_url: str) -> str:
        """辅助方法：上传文件并返回 file_id"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Hello Faber")
            temp_path = f.name

        try:
            with open(temp_path, "rb") as f:
                response = client.post(
                    f"{api_url}/files",
                    files={"file": ("hello.txt", f, "text/plain")},
                )
            assert response.status_code == 200

            data = response.json()
            assert data["code"] == 200
            assert "id" in data["data"]
            return data["data"]["id"]
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_upload_file(self, client, api_url: str):
        """测试 POST /api/files 文件上传"""
        file_id = self._upload_file(client, api_url)
        assert file_id

    def test_get_file_info_and_download(self, client, api_url: str):
        """测试 GET /api/files/{id} 和 GET /api/files/{id}/download"""
        file_id = self._upload_file(client, api_url)

        # 获取文件信息
        response = client.get(f"{api_url}/files/{file_id}")
        assert response.status_code == 200
        assert response.json()["code"] == 200
        assert response.json()["data"]["id"] == file_id

        # 下载文件
        response = client.get(f"{api_url}/files/{file_id}/download")
        assert response.status_code == 200
        assert response.headers.get("content-disposition") is not None
