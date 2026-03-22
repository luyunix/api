import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.infrastructure.logging import setup_logging
from app.infrastructure.storage.cos import get_cos
from app.infrastructure.storage.oss import get_oss
from app.infrastructure.storage.postgres import get_postgres
from app.infrastructure.storage.redis import get_redis
from app.interfaces.endpoints.routes import router
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import get_agent_service
from core.config import get_settings


# 获取项目根目录
def get_project_root() -> Path:
    """获取项目根目录"""
    # 当前文件所在目录
    current_file = Path(__file__).resolve()
    # app/main.py -> 项目根目录
    return current_file.parent.parent


# 切换到项目根目录（确保 alembic.ini 等配置文件能被找到）
project_root = get_project_root()
os.chdir(project_root)
sys.path.insert(0, str(project_root))

# 1.加载配置信息
settings = get_settings()

# 2.初始化日志系统
setup_logging()
logger = logging.getLogger()

# 3.定义FastAPI路由tags标签
openapi_tags = [
    {
        "name": "状态模块",
        "description": "包含 **状态监测** 等API 接口，用于监测系统的运行状态。"
    }
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """创建FastAPI应用生命周期上下文管理器"""
    # 0.重新初始化日志系统(uvicorn启动时dictConfig会影响根日志处理器，需要在此重新配置)
    setup_logging()

    # 1.日志打印代码已经开始执行了
    logger.info("Faber正在初始化")

    # 2.运行数据库迁移(将数据同步到生产环境)
    logger.info("[1/4] 开始数据库迁移...")
    alembic_ini_path = project_root / "alembic.ini"
    alembic_cfg = Config(str(alembic_ini_path))
    command.upgrade(alembic_cfg, "head")
    # alembic 会覆盖日志配置，需要重新初始化
    setup_logging()
    _logger = logging.getLogger()
    _logger.info("[1/4] 数据库迁移完成")

    # 3.初始化Redis/Postgres/Oss/Cos客户端
    logger.info("[2/4] 初始化Redis...")
    try:
        await asyncio.wait_for(get_redis().init(), timeout=5.0)
        logger.info("[2/4] Redis初始化完成")
    except asyncio.TimeoutError:
        logger.error("[2/4] Redis初始化超时(5s)，请检查Redis服务是否启动")
        raise

    logger.info("[3/4] 初始化Postgres...")
    try:
        await asyncio.wait_for(get_postgres().init(), timeout=10.0)
        logger.info("[3/4] Postgres初始化完成")
    except asyncio.TimeoutError:
        logger.error("[3/4] Postgres初始化超时(10s)，请检查Postgres服务是否启动")
        raise

    logger.info("[4/5] 初始化OSS...")
    try:
        # await asyncio.wait_for(get_oss().init(), timeout=5.0)
        logger.info("[4/5] OSS初始化完成")
    except asyncio.TimeoutError:
        logger.error("[4/5] OSS初始化超时(5s)，请检查网络连接")
        raise

    logger.info("[5/5] 初始化COS...")
    try:
        await asyncio.wait_for(get_cos().init(), timeout=5.0)
        logger.info("[5/5] COS初始化完成")
    except asyncio.TimeoutError:
        logger.error("[5/5] COS初始化超时(5s)，请检查网络连接")
        raise

    try:
        # 4.lifespan分界点
        yield
    finally:
        try:
            # 5.等待agent服务关闭
            logger.info("Faber正在关闭")
            await asyncio.wait_for(get_agent_service().shutdown(), timeout=30.0)
            logger.info("Agent服务成功关闭")
        except asyncio.TimeoutError:
            logger.warning("Agent服务关闭超时, 强制关闭, 部分任务将被释放")
        except Exception as e:
            logger.error(f"Agent服务关闭期间出现错误: {str(e)}")

        # 6.关闭其他应用
        await get_redis().shutdown()
        await get_postgres().shutdown()
        await get_oss().shutdown()
        await get_cos().shutdown()

        logger.info("Faber应用关闭成功")


# 4.创建Faber应用实例
app = FastAPI(
    title="Faber通用智能体",
    description="Faber是一个通用的AI Agent系统，可以完全私有部署，使用A2A+MCP连接Agent/Tool，同时支持在沙箱中运行各种内置工具和操作",
    lifespan=lifespan,
    openapi_tags=openapi_tags,
    version="1.0.0",
)

# 5.配置CORS中间件，解决跨域问题
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 6.注册错误处理器
register_exception_handlers(app)

# 7.集成路由
app.include_router(router, prefix="/api")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
