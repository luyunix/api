from fastapi import APIRouter, Depends

from app.application.services.auth_service import AuthService
from app.domain.models.user import User
from app.interfaces.auth import get_current_user
from app.interfaces.schemas import Response
from app.interfaces.schemas.auth import (
    AuthResponse,
    LoginRequest,
    RegisterRequest,
    UpdateProfileRequest,
    UserInfo,
)
from app.interfaces.service_dependencies import get_auth_service

router = APIRouter(prefix="/auth", tags=["认证模块"])


def to_user_info(user: User) -> UserInfo:
    return UserInfo(
        id=user.id,
        email=user.email,
        username=user.username,
        avatar_url=user.avatar_url,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


@router.post("/register", response_model=Response[AuthResponse], summary="注册用户")
async def register(
        request: RegisterRequest,
        auth_service: AuthService = Depends(get_auth_service),
) -> Response[AuthResponse]:
    user, token = await auth_service.register(request.email, request.password, request.username)
    return Response.success(
        msg="注册成功",
        data=AuthResponse(access_token=token, user=to_user_info(user)),
    )


@router.post("/login", response_model=Response[AuthResponse], summary="登录")
async def login(
        request: LoginRequest,
        auth_service: AuthService = Depends(get_auth_service),
) -> Response[AuthResponse]:
    user, token = await auth_service.login(request.email, request.password)
    return Response.success(
        msg="登录成功",
        data=AuthResponse(access_token=token, user=to_user_info(user)),
    )


@router.get("/me", response_model=Response[UserInfo], summary="获取当前用户")
async def me(current_user: User = Depends(get_current_user)) -> Response[UserInfo]:
    return Response.success(msg="获取当前用户成功", data=to_user_info(current_user))


@router.post("/me", response_model=Response[UserInfo], summary="更新当前用户资料")
async def update_me(
        request: UpdateProfileRequest,
        current_user: User = Depends(get_current_user),
        auth_service: AuthService = Depends(get_auth_service),
) -> Response[UserInfo]:
    user = await auth_service.update_profile(current_user.id, request.username, request.avatar_url)
    return Response.success(msg="更新用户资料成功", data=to_user_info(user))
