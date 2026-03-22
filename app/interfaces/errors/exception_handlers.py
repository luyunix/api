import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.application.errors.exceptions import AppException
from app.interfaces.schemas import Response

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    """处理Faber项目中所有的异常并进行统一处理，涵盖：自定义业务状态异常、HTTP异常、通用异常"""

    @app.exception_handler(AppException)
    async def app_exception_handler(req: Request, e: AppException) -> JSONResponse:
        """处理Faber业务异常，将所有状态统一响应结构"""
        logger.error(f"AppException: {e.msg}")
        return JSONResponse(
            status_code=e.status_code,
            content=Response(
                code=e.status_code,
                msg=e.msg,
                data={}
            ).model_dump(),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(req: Request, e: HTTPException) -> JSONResponse:
        """处理FastAPI抛出的http异常，将所有状态统一响应结构"""
        logger.error(f"HTTPException: {e.detail}")
        return JSONResponse(
            status_code=e.status_code,
            content=Response(
                code=e.status_code,
                msg=e.detail,
                data={}
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    async def exception_handler(req: Request, e: Exception) -> JSONResponse:
        """处理Faber中抛出的未定义的任意一场，将状态码统一设置为500"""
        # 记录完整堆栈信息
        logger.exception(f"Unhandled Exception: {str(e)}")
        # 同时在控制台打印堆栈（开发调试时使用）
        print("=" * 60)
        print(f"EXCEPTION: {str(e)}")
        print("-" * 60)
        traceback.print_exc()
        print("=" * 60)
        return JSONResponse(
            status_code=500,
            content=Response(
                code=500,
                msg=f"服务器异常: {str(e)}",
                data={"traceback": traceback.format_exc()} if app.debug else {},
            ).model_dump()
        )
