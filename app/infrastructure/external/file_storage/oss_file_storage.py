import logging
import os.path
import uuid
from datetime import datetime
from typing import Tuple, BinaryIO, Callable

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from app.domain.external.file_storage import FileStorage
from app.domain.models.file import File
from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.storage.oss import Oss

logger = logging.getLogger(__name__)


class OssFileStorage(FileStorage):
    """基于OSS的文件存储扩展"""

    def __init__(
            self,
            bucket: str,
            oss: Oss,
            uow_factory: Callable[[], IUnitOfWork],
    ) -> None:
        """构造函数，完成oss文件存储桶扩展初始化"""
        self.bucket = bucket
        self.oss = oss
        self._uow_factory = uow_factory
        self._uow = uow_factory()

    async def upload_file(self, upload_file: UploadFile) -> File:
        """根据传递的文件源将文件上传到阿里云oss"""
        try:
            # 1.生成随机的uuid作为文件id并获取文件扩展名
            file_id = str(uuid.uuid4())
            _, file_extension = os.path.splitext(upload_file.filename)
            if not file_extension:
                file_extension = ""

            # 2.生成日期路径并拼接最终key
            date_path = datetime.now().strftime("%Y/%m/%d")
            oss_key = f"{date_path}/{file_id}{file_extension}"

            # 3.使用fastapi的线程池来上传文件
            # OSS 直接上传文件流
            await run_in_threadpool(
                self.oss.client.put_object,
                oss_key,
                upload_file.file,
            )
            logger.info(f"文件上传成功: {upload_file.filename} (ID: {file_id})")

            # 4.构建file模型并将数据存储到数据库中
            file = File(
                id=file_id,
                filename=upload_file.filename,
                key=oss_key,
                extension=file_extension,
                mime_type=upload_file.content_type or "",
                size=upload_file.size,
            )
            async with self._uow:
                await self._uow.file.save(file)

            return file
        except Exception as e:
            logger.error(f"上传文件[{upload_file.filename}]失败: {str(e)}")
            raise

    async def download_file(self, file_id: str) -> Tuple[BinaryIO, File]:
        """根据文件id查询数据并下载文件"""
        try:
            # 1.查询对应的文件记录是否存在
            async with self._uow:
                file = await self._uow.file.get_by_id(file_id)
            if not file:
                raise ValueError(f"该文件不存在, 文件id: {file_id}")

            # 2.使用线程池来下载文件
            # OSS 返回的是文件对象，需要读取内容
            response = await run_in_threadpool(
                self.oss.client.get_object,
                file.key,
            )

            # 3.返回文件流+文件信息
            return response, file
        except Exception as e:
            logger.error(f"下载文件[{file_id}]失败: {str(e)}")
            raise
