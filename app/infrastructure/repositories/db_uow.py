import asyncio
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.domain.repositories.uow import IUnitOfWork
from .db_episodic_memory_repository import DBEpisodicMemoryRepository
from .db_file_repository import DBFileRepository
from .db_session_repository import DBSessionRepository

logger = logging.getLogger(__name__)


class DBUnitOfWork(IUnitOfWork):
    """基于Postgres数据库的UoW实例"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        """构造函数，完成UoW类初始化"""
        self.session_factory = session_factory
        self.db_session: Optional[AsyncSession] = None

    async def commit(self):
        """提交数据库持久化"""
        await self.db_session.commit()

    async def rollback(self):
        """数据库回退操作"""
        await self.db_session.rollback()

    async def __aenter__(self) -> "DBUnitOfWork":
        """进入UoW操作上下文管理器的逻辑"""
        # 1.为每个上下文开启一个新的会话
        self.db_session = self.session_factory()

        # 2.初始化所有数据库仓库
        self.file = DBFileRepository(db_session=self.db_session)
        self.session = DBSessionRepository(db_session=self.db_session)
        self.episodic_memory = DBEpisodicMemoryRepository(db_session=self.db_session)

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """退出上下文时执行的逻辑，如果出现异常则回滚，否则提交

        当SSE客户端断开连接时，sse_starlette的anyio cancel scope会取消当前Task中
        所有await操作，包括此处的commit/rollback/close以及SQLAlchemy内部的
        asyncio.shield。如果不妥善处理CancelledError，SQLAlchemy在尝试优雅地
        终止连接时会被取消，从而产生ERROR日志并可能污染连接池。

        解决方案：检测到CancelledError时，将清理工作放到一个独立的asyncio Task
        中执行。新Task不受触发此次__aexit__的anyio cancel scope影响，可以正常
        完成会话关闭与连接归还/终止。
        """
        session = self.db_session
        cancelled = exc_type is asyncio.CancelledError

        async def _cleanup(
            _session: Optional[AsyncSession] = session,
            _exc_type: Optional[type] = exc_type,
        ) -> None:
            try:
                if _exc_type and _exc_type is not asyncio.CancelledError:
                    await _session.rollback()
                elif not _exc_type:
                    await _session.commit()
                # 如果是CancelledError导致的退出，跳过commit/rollback，直接close
            except asyncio.CancelledError:
                logger.warning("UoW提交/回滚操作被取消(可能是客户端断开连接)")
            except Exception as e:
                logger.warning(f"UoW提交/回滚操作失败: {e}")
            finally:
                try:
                    if _session:
                        await _session.close()
                except asyncio.CancelledError:
                    logger.warning("UoW关闭数据库会话被取消(可能是客户端断开连接)")
                except Exception as e:
                    logger.warning(f"UoW关闭数据库会话失败: {e}")

        if cancelled:
            # 将清理任务投递到事件循环后不再等待，使其逃离当前cancel scope
            try:
                asyncio.create_task(_cleanup())
            except RuntimeError:
                # 事件循环已关闭（如应用正在关闭），无法创建后台任务
                logger.warning("无法创建后台任务清理UoW，事件循环已关闭")
        else:
            await _cleanup()
