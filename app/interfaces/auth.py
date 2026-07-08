from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.application.errors.exceptions import UnauthorizedError
from app.application.services.auth_service import AuthService
from app.domain.models.user import User
from app.infrastructure.storage.postgres import get_uow

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> User:
    """解析 Bearer token 并返回当前用户。"""
    if not credentials or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError("请先登录")

    payload = AuthService.decode_access_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedError("无效的登录凭证")

    async with get_uow() as uow:
        user = await uow.user.get_by_id(user_id)
    if not user or not user.is_active:
        raise UnauthorizedError("用户不存在或已被禁用")

    return user
