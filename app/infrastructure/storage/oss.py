import logging
from functools import lru_cache
from typing import Optional

import oss2

from core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class Oss:
    """阿里云OSS对象存储"""

    def __init__(self):
        """构造函数，完成配置获取+Oss客户端初始化赋值"""
        self._settings: Settings = get_settings()
        self._client: Optional[oss2.Bucket] = None

    async def init(self) -> None:
        """完成oss阿里云对象存储客户端的创建"""
        # 1.判断客户端是否存在，如果存在则记录日志并终止程序
        if self._client is not None:
            logger.warning("Oss阿里云对象存储已初始化，无需重复操作")
            return

        try:
            # 2.创建oss认证对象
            auth = oss2.Auth(
                self._settings.oss_access_key_id,
                self._settings.oss_access_key_secret,
            )
            # 3.创建Bucket实例
            self._client = oss2.Bucket(
                auth,
                self._settings.oss_endpoint,
                self._settings.oss_bucket,
            )
            # 4.测试连接
            self._client.get_bucket_info()
            logger.info("Oss阿里云对象存储初始化成功")
        except Exception as e:
            logger.error(f"Oss阿里云对象存储初始化失败: {str(e)}")
            raise

    async def shutdown(self) -> None:
        """关闭oss阿里云对象存储"""
        if self._client is not None:
            self._client = None
            logger.info("关闭阿里云Oss对象存储成功")

        get_oss.cache_clear()

    @property
    def client(self) -> oss2.Bucket:
        """只读属性，返回阿里云Oss对象存储客户端"""
        if self._client is None:
            raise RuntimeError("阿里云Oss对象存储未初始化，请调用init()完成初始化")
        return self._client


@lru_cache()
def get_oss() -> Oss:
    """获取阿里云oss对象存储"""
    return Oss()
