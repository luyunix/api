from datetime import datetime

from sqlalchemy import DateTime, String, PrimaryKeyConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from app.domain.models.app_config import AppConfig


class UserConfigModel(Base):
    """用户级配置 ORM 模型。"""

    __tablename__ = "user_configs"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", name="pk_user_configs_user_id"),
    )

    user_id: Mapped[str] = mapped_column(String(255), nullable=False, primary_key=True)
    config: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        onupdate=datetime.now,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )

    @classmethod
    def from_domain(cls, user_id: str, app_config: AppConfig) -> "UserConfigModel":
        return cls(user_id=user_id, config=app_config.model_dump(mode="json"))

    def to_domain(self) -> AppConfig:
        return AppConfig.model_validate(self.config)

    def update_from_domain(self, app_config: AppConfig) -> None:
        self.config = app_config.model_dump(mode="json")
