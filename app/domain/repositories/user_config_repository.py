from typing import Optional, Protocol

from app.domain.models.app_config import AppConfig


class UserConfigRepository(Protocol):
    """用户级应用配置仓库协议。"""

    async def get_by_user_id(self, user_id: str) -> Optional[AppConfig]:
        """获取指定用户的配置。"""
        ...

    async def save(self, user_id: str, app_config: AppConfig) -> None:
        """保存指定用户的配置。"""
        ...
