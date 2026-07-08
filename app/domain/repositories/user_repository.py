from typing import Optional, Protocol

from app.domain.models.user import User


class UserRepository(Protocol):
    """用户仓库协议定义。"""

    async def add(self, user: User) -> None:
        """新增用户。"""
        ...

    async def get_by_id(self, user_id: str) -> Optional[User]:
        """根据用户 id 查询用户。"""
        ...

    async def get_by_email(self, email: str) -> Optional[User]:
        """根据邮箱查询用户。"""
        ...

    async def update(self, user: User) -> None:
        """更新用户。"""
        ...

    async def touch_last_login(self, user_id: str) -> None:
        """更新最后登录时间。"""
        ...
