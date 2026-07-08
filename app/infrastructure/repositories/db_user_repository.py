from datetime import datetime
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.user import User
from app.domain.repositories.user_repository import UserRepository
from app.infrastructure.models.user import UserModel


class DBUserRepository(UserRepository):
    """基于 Postgres 的用户仓库。"""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def add(self, user: User) -> None:
        self.db_session.add(UserModel.from_domain(user))
        await self.db_session.flush()

    async def get_by_id(self, user_id: str) -> Optional[User]:
        stmt = select(UserModel).where(UserModel.id == user_id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def get_by_email(self, email: str) -> Optional[User]:
        stmt = select(UserModel).where(UserModel.email == email.lower())
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def update(self, user: User) -> None:
        stmt = select(UserModel).where(UserModel.id == user.id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        if record:
            record.update_from_domain(user)

    async def touch_last_login(self, user_id: str) -> None:
        stmt = (
            update(UserModel)
            .where(UserModel.id == user_id)
            .values(last_login_at=datetime.now())
        )
        await self.db_session.execute(stmt)
