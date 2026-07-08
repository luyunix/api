import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, PrimaryKeyConstraint, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from app.domain.models.user import User


class UserModel(Base):
    """用户 ORM 模型。"""

    __tablename__ = "users"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_users_id"),
        UniqueConstraint("email", name="uq_users_email"),
    )

    id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    avatar_url: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        server_default=text("''::character varying"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
    last_login_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
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
    def from_domain(cls, user: User) -> "UserModel":
        """从领域模型构建 ORM 模型。"""
        return cls(**user.model_dump(mode="json", exclude={"updated_at", "created_at"}))

    def to_domain(self) -> User:
        """转换为领域模型。"""
        return User.model_validate(self, from_attributes=True)

    def update_from_domain(self, user: User) -> None:
        """从领域模型更新 ORM 数据。"""
        data = user.model_dump(mode="json", exclude={"created_at", "updated_at"})
        for field, value in data.items():
            setattr(self, field, value)
