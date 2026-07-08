from fastapi import APIRouter

from fastapi import Depends

from app.interfaces.auth import get_current_user
from . import status_routes, app_config_routes, file_routes, session_routes, auth_routes


def create_api_routes() -> APIRouter:
    """创建API路由，涵盖整个项目的所有路由管理"""
    # 1.创建APIRouter实例
    api_router = APIRouter()

    # 2.将各个模块添加到api_router中
    api_router.include_router(status_routes.router)
    api_router.include_router(auth_routes.router)
    api_router.include_router(app_config_routes.router, dependencies=[Depends(get_current_user)])
    api_router.include_router(file_routes.router)
    api_router.include_router(session_routes.router)

    # 3.返回api路由实例
    return api_router


router = create_api_routes()
