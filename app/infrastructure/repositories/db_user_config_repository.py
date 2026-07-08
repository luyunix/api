from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.app_config import AppConfig
from app.domain.repositories.user_config_repository import UserConfigRepository
from app.infrastructure.models.user_config import UserConfigModel


class DBUserConfigRepository(UserConfigRepository):
    """基于 Postgres 的用户级配置仓库。"""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def get_by_user_id(self, user_id: str) -> Optional[AppConfig]:
        stmt = select(UserConfigModel).where(UserConfigModel.user_id == user_id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def save(self, user_id: str, app_config: AppConfig) -> None:
        stmt = select(UserConfigModel).where(UserConfigModel.user_id == user_id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        if not record:
            self.db_session.add(UserConfigModel.from_domain(user_id, app_config))
            return

        record.update_from_domain(app_config)
