import logging

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.agent_service import AgentService
from app.application.services.app_config_service import AppConfigService
from app.application.services.file_service import FileService
from app.application.services.session_service import SessionService
from app.application.services.status_service import StatusService
from app.infrastructure.external.file_storage.cos_file_storage import CosFileStorage
from app.infrastructure.external.file_storage.oss_file_storage import OssFileStorage
from app.infrastructure.external.health_checker.postgres_health_checker import PostgresHealthChecker
from app.infrastructure.external.health_checker.redis_health_checker import RedisHealthChecker
from app.infrastructure.external.json_parser.repair_json_parser import RepairJSONParser
from app.infrastructure.external.llm.openai_llm import OpenAILLM
from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox
from app.infrastructure.external.search.bing_search import BingSearchEngine
from app.infrastructure.external.task.redis_stream_task import RedisStreamTask
from app.infrastructure.repositories.file_app_config_repository import FileAppConfigRepository
from app.domain.services.memory.episodic_memory_service import EpisodicMemoryService
from app.domain.services.memory.memory_budget import MemoryCompactor
from app.domain.services.memory.memory_summarizer import MemorySummarizer
from app.infrastructure.external.embedder.openai_embedder import OpenAIEmbedder
from app.infrastructure.memory.db_memory_batch_writer import DBMemoryBatchWriter
from app.infrastructure.storage.cos import Cos, get_cos
from app.infrastructure.storage.oss import Oss, get_oss
from app.infrastructure.storage.postgres import get_db_session, get_uow
from app.infrastructure.storage.redis import RedisClient, get_redis
from core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# 全局 MemoryBatchWriter 单例(延迟初始化)
_memory_batch_writer: DBMemoryBatchWriter | None = None


def get_memory_batch_writer() -> DBMemoryBatchWriter:
    """获取记忆批量写入器单例"""
    global _memory_batch_writer
    if _memory_batch_writer is None:
        _memory_batch_writer = DBMemoryBatchWriter(
            uow_factory=get_uow,
            batch_size=10,
            flush_interval=3.0,
        )
    return _memory_batch_writer


def get_app_config_service() -> AppConfigService:
    """获取应用配置服务"""
    # 1.获取数据仓库并打印日志
    logger.info("加载获取AppConfigService")
    file_app_config_repository = FileAppConfigRepository(settings.app_config_filepath)

    # 2.实例化AppConfigService
    return AppConfigService(app_config_repository=file_app_config_repository)


def get_status_service(
        db_session: AsyncSession = Depends(get_db_session),
        redis_client: RedisClient = Depends(get_redis),
) -> StatusService:
    """获取状态服务"""
    # 1.初始化postgres和redis健康检查
    postgres_checker = PostgresHealthChecker(db_session)
    redis_checker = RedisHealthChecker(redis_client)

    # 2.创建服务并返回
    logger.info("加载获取StatusService")
    return StatusService(checkers=[postgres_checker, redis_checker])


def get_file_service(
        oss: Oss = Depends(get_oss)
) -> FileService:
    # 1.初始化文件仓库和文件存储桶
    file_storage = OssFileStorage(
        bucket=settings.oss_bucket,
        oss=oss,
        uow_factory=get_uow,
    )

    # 2.构建服务并返回
    return FileService(
        uow_factory=get_uow,
        file_storage=file_storage,
    )


def get_file_service_cos(
        cos: Cos = Depends(get_cos)
) -> FileService:
    """使用腾讯云COS的文件服务"""
    # 1.初始化文件仓库和文件存储桶
    file_storage = CosFileStorage(
        bucket=settings.cos_bucket,
        cos=cos,
        uow_factory=get_uow,
    )

    # 2.构建服务并返回
    return FileService(
        uow_factory=get_uow,
        file_storage=file_storage,
    )


def get_session_service() -> SessionService:
    return SessionService(uow_factory=get_uow, sandbox_cls=DockerSandbox)


def get_agent_service(
        oss: Oss = Depends(get_oss),
) -> AgentService:
    # 1.获取应用配置信息(读取配置需要实时获取,所以不配置缓存)
    app_config_repository = FileAppConfigRepository(config_path=settings.app_config_filepath)
    app_config = app_config_repository.load()

    # 2.构建依赖实例
    llm = OpenAILLM(app_config.llm_config)
    file_storage = OssFileStorage(
        bucket=settings.oss_bucket,
        oss=oss,
        uow_factory=get_uow,
    )

    # 3.创建记忆压缩器 + 情景记忆服务
    memory_compactor, episodic_memory_service = _build_memory_services(app_config, llm)

    # 4.实例Agent服务并返回
    return AgentService(
        uow_factory=get_uow,
        llm=llm,
        agent_config=app_config.agent_config,
        mcp_config=app_config.mcp_config,
        a2a_config=app_config.a2a_config,
        sandbox_cls=DockerSandbox,
        task_cls=RedisStreamTask,
        json_parser=RepairJSONParser(),
        search_engine=BingSearchEngine(),
        file_storage=file_storage,
        memory_batch_writer=get_memory_batch_writer(),
        memory_compactor=memory_compactor,
        episodic_memory_service=episodic_memory_service,
    )


def _build_memory_services(app_config, llm):
    """构建记忆压缩器与情景记忆服务

    - 压缩预算 = context_window - max_tokens(输出) - reserve(1024)
    - 情景记忆：embedding_config.enabled 时构建 Embedder + EpisodicMemoryService，
      否则 EpisodicMemoryService 的 embedder 为 None（降级为空操作）。
    """
    # 1.记忆摘要器（压缩时生成 LLM 摘要）
    summarizer = MemorySummarizer(llm=llm)

    # 2.压缩预算（可用上下文）
    usable_context = max(
        1024,
        app_config.agent_config.context_window - llm.max_tokens - 1024,
    )
    memory_compactor = MemoryCompactor(usable_context=usable_context, summarizer=summarizer)

    # 3.Embedder（情景记忆启用时）
    embedder = None
    embedding_config = app_config.embedding_config
    if embedding_config.enabled and embedding_config.api_key:
        try:
            embedder = OpenAIEmbedder(embedding_config)
            logger.info(f"情景记忆已启用: model={embedding_config.model_name}, dim={embedding_config.dimension}")
        except Exception as e:
            logger.warning(f"初始化 Embedder 失败，情景记忆将不可用: {e}")
            embedder = None
    else:
        logger.info("情景记忆未启用（embedding_config.enabled=false 或缺 api_key）")

    # 4.情景记忆服务
    episodic_memory_service = EpisodicMemoryService(
        embedder=embedder,
        uow_factory=get_uow,
        llm=llm,
        top_k=3,
        max_distance=0.6,
    )

    return memory_compactor, episodic_memory_service


def get_agent_service_cos(
        cos: Cos = Depends(get_cos),
) -> AgentService:
    """使用腾讯云COS的Agent服务"""
    # 1.获取应用配置信息(读取配置需要实时获取,所以不配置缓存)
    app_config_repository = FileAppConfigRepository(config_path=settings.app_config_filepath)
    app_config = app_config_repository.load()

    # 2.构建依赖实例
    llm = OpenAILLM(app_config.llm_config)
    file_storage = CosFileStorage(
        bucket=settings.cos_bucket,
        cos=cos,
        uow_factory=get_uow,
    )

    # 3.创建记忆压缩器 + 情景记忆服务
    memory_compactor, episodic_memory_service = _build_memory_services(app_config, llm)

    # 4.实例Agent服务并返回
    return AgentService(
        uow_factory=get_uow,
        llm=llm,
        agent_config=app_config.agent_config,
        mcp_config=app_config.mcp_config,
        a2a_config=app_config.a2a_config,
        sandbox_cls=DockerSandbox,
        task_cls=RedisStreamTask,
        json_parser=RepairJSONParser(),
        search_engine=BingSearchEngine(),
        file_storage=file_storage,
        memory_batch_writer=get_memory_batch_writer(),
        memory_compactor=memory_compactor,
        episodic_memory_service=episodic_memory_service,
    )
