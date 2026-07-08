import logging
from pathlib import Path
from typing import Optional

import yaml
from filelock import FileLock

from app.application.errors.exceptions import ServerRequestsError
from app.domain.models.app_config import AppConfig, LLMConfig, AgentConfig, MCPConfig, A2AConfig
from app.domain.repositories.app_config_repository import AppConfigRepository
from core.config import get_settings

logger = logging.getLogger(__name__)


class FileAppConfigRepository(AppConfigRepository):
    """基于本地文件的App配置数据仓库"""

    def __init__(self, config_path: str, user_id: Optional[str] = None) -> None:
        """构造函数，完成文件配置仓库的相关信息初始化"""
        # 1.获取当前项目的根目录
        root_dir = Path.cwd()

        # 2.拼接配置文件路径并校验基础信息
        base_config_path = root_dir.joinpath(root_dir, config_path)
        if user_id:
            safe_user_id = "".join(ch for ch in user_id if ch.isalnum() or ch in ("-", "_"))
            self._config_path = base_config_path.parent / "users" / f"{safe_user_id}.yaml"
            self._base_config_path = base_config_path
        else:
            self._config_path = base_config_path
            self._base_config_path = None
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = self._config_path.with_suffix(".lock")  # 文件锁

    @staticmethod
    def _default_app_config() -> AppConfig:
        return AppConfig(
            llm_config=LLMConfig(),
            agent_config=AgentConfig(),
            mcp_config=MCPConfig(),
            a2a_config=A2AConfig(),
        )

    def _load_base_app_config(self) -> AppConfig:
        """用户配置首次创建时，以全局配置作为初始模板。"""
        if not self._base_config_path or not self._base_config_path.exists():
            return self._default_app_config()

        try:
            with open(self._base_config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                return AppConfig.model_validate(data) if data else self._default_app_config()
        except Exception:
            logger.warning("读取全局配置作为用户默认模板失败，回退到内置默认配置")
            return self._default_app_config()

    def _create_default_app_config_if_not_exists(self):
        """如果配置文件不存在，则使用默认配置并写入到本地文件"""
        if not self._config_path.exists():
            default_app_config = self._load_base_app_config()
            self.save(default_app_config)

    def load(self) -> Optional[AppConfig]:
        """从本地yaml文件中加载应用配置"""
        # 1.创建默认配置确保文件存在
        self._create_default_app_config_if_not_exists()

        try:
            # 2.打开配置文件并加载为AppConfig
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                app_config = AppConfig.model_validate(data) if data else None
                if app_config:
                    self._apply_env_overrides(app_config)
                return app_config
        except Exception as e:
            logger.error(f"读取应用配置失败: {str(e)}")
            raise ServerRequestsError("读取应用配置失败，请稍后尝试")

    @staticmethod
    def _apply_env_overrides(app_config: AppConfig) -> None:
        """用 .env / 环境变量中的非空 LLM 配置覆盖文件配置。"""
        settings = get_settings()
        llm_config_data = app_config.llm_config.model_dump(mode="json")
        if settings.llm_base_url:
            llm_config_data["base_url"] = settings.llm_base_url
        if settings.llm_api_key:
            llm_config_data["api_key"] = settings.llm_api_key
        if settings.llm_model_name:
            llm_config_data["model_name"] = settings.llm_model_name
        if settings.llm_temperature is not None:
            llm_config_data["temperature"] = settings.llm_temperature
        if settings.llm_max_tokens is not None:
            llm_config_data["max_tokens"] = settings.llm_max_tokens
        app_config.llm_config = LLMConfig.model_validate(llm_config_data)

    def save(self, app_config: AppConfig) -> None:
        """将app_config存储到本地yaml配置"""
        # 1.写入之前先上锁
        lock = FileLock(self._lock_file, timeout=5)

        try:
            with lock:
                # 2.将app_config转换成json
                data_to_dump = app_config.model_dump(mode="json")

                # 3.打开yaml文件并写入
                with open(self._config_path, "w", encoding="utf-8") as f:
                    yaml.dump(data_to_dump, f, allow_unicode=True, sort_keys=False)
        except TimeoutError:
            logger.error("无法获取配置文件")
            raise ServerRequestsError("写入配置文件失败，请稍后尝试")
