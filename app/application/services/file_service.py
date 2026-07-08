from typing import Tuple, BinaryIO, Callable, Optional

from fastapi import UploadFile

from app.application.errors.exceptions import ForbiddenError, NotFoundError
from app.domain.external.file_storage import FileStorage
from app.domain.models.file import File
from app.domain.repositories.uow import IUnitOfWork


class FileService:
    """Faber文件系统服务"""

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            file_storage: FileStorage,
    ) -> None:
        """构造函数，完成文件服务的初始化"""
        self.file_storage = file_storage
        self._uow_factory = uow_factory
        self._uow = uow_factory()

    async def upload_file(self, upload_file: UploadFile, user_id: str, session_id: Optional[str] = None) -> File:
        """将传递的文件上传到阿里云oss并记录上传数据"""
        file = await self.file_storage.upload_file(upload_file=upload_file)
        file.user_id = user_id

        async with self._uow:
            await self._uow.file.save(file)

        if session_id:
            async with self._uow:
                session = await self._uow.session.get_by_id(session_id)
                if not session or session.user_id != user_id:
                    raise NotFoundError("该会话不存在，请核实后重试")
                await self._uow.session.add_file(session_id, file)

        return file

    async def get_file_info(self, file_id: str, user_id: str) -> File:
        """根据传递的文件id获取文件信息"""
        async with self._uow:
            file = await self._uow.file.get_by_id(file_id)
        if not file:
            raise NotFoundError(f"该文件[{file_id}]不存在")
        if file.user_id != user_id:
            raise ForbiddenError("无权限访问该文件")
        return file

    async def download_file(self, file_id: str, user_id: str) -> Tuple[BinaryIO, File]:
        """根据传递的文件id下载文件"""
        file = await self.get_file_info(file_id, user_id)
        file_data, _ = await self.file_storage.download_file(file_id)
        return file_data, file
