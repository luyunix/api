import base64
import hashlib
import hmac
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import jwt

from app.application.errors.exceptions import BadRequestError, UnauthorizedError
from app.domain.models.user import User
from app.domain.repositories.uow import IUnitOfWork
from core.config import get_settings

PASSWORD_ITERATIONS = 260_000
PASSWORD_ALGORITHM = "pbkdf2_sha256"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthService:
    """注册、登录与 JWT 签发服务。"""

    def __init__(self, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def register(self, email: str, password: str, username: Optional[str] = None) -> tuple[User, str]:
        email = email.lower().strip()
        username = (username or email.split("@", 1)[0]).strip()
        self._validate_email(email)
        self._validate_password(password)

        async with self._uow_factory() as uow:
            existed = await uow.user.get_by_email(email)
            if existed:
                raise BadRequestError("该邮箱已注册")

            user = User(
                email=email,
                username=username,
                password_hash=self.hash_password(password),
            )
            await uow.user.add(user)

        return user, self.create_access_token(user)

    async def login(self, email: str, password: str) -> tuple[User, str]:
        email = email.lower().strip()
        self._validate_email(email)
        async with self._uow_factory() as uow:
            user = await uow.user.get_by_email(email)
            if not user or not self.verify_password(password, user.password_hash):
                raise UnauthorizedError("邮箱或密码错误")
            if not user.is_active:
                raise UnauthorizedError("用户已被禁用")
            await uow.user.touch_last_login(user.id)

        return user, self.create_access_token(user)

    async def update_profile(self, user_id: str, username: str, avatar_url: str = "") -> User:
        username = username.strip()
        if not username:
            raise BadRequestError("用户名不能为空")

        async with self._uow_factory() as uow:
            user = await uow.user.get_by_id(user_id)
            if not user:
                raise UnauthorizedError("用户不存在或登录已失效")
            user.username = username
            user.avatar_url = avatar_url.strip()
            await uow.user.update(user)
            return user

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(password) < 8:
            raise BadRequestError("密码至少需要 8 位")

    @staticmethod
    def _validate_email(email: str) -> None:
        if not EMAIL_RE.match(email):
            raise BadRequestError("邮箱格式不正确")

    @staticmethod
    def hash_password(password: str) -> str:
        salt = os.urandom(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
        return "$".join([
            PASSWORD_ALGORITHM,
            str(PASSWORD_ITERATIONS),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ])

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        try:
            algorithm, iterations, salt_b64, digest_b64 = password_hash.split("$", 3)
            if algorithm != PASSWORD_ALGORITHM:
                return False
            salt = base64.b64decode(salt_b64.encode("ascii"))
            expected = base64.b64decode(digest_b64.encode("ascii"))
            actual = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt,
                int(iterations),
            )
            return hmac.compare_digest(actual, expected)
        except Exception:
            return False

    @staticmethod
    def create_access_token(user: User) -> str:
        settings = get_settings()
        now = datetime.now(timezone.utc)
        payload = {
            "sub": user.id,
            "email": user.email,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=settings.auth_access_token_expire_minutes)).timestamp()),
        }
        return jwt.encode(payload, settings.auth_secret_key, algorithm=settings.auth_algorithm)

    @staticmethod
    def decode_access_token(token: str) -> dict:
        settings = get_settings()
        try:
            return jwt.decode(token, settings.auth_secret_key, algorithms=[settings.auth_algorithm])
        except jwt.ExpiredSignatureError as e:
            raise UnauthorizedError("登录已过期，请重新登录") from e
        except jwt.InvalidTokenError as e:
            raise UnauthorizedError("无效的登录凭证") from e
