import logging
import uuid
from typing import Callable, List, Optional

from app.application.errors.exceptions import BadRequestError, NotFoundError
from app.domain.models.app_config import AppConfig, LLMConfig, AgentConfig, MCPConfig, A2AConfig, A2AServerConfig
from app.domain.repositories.app_config_repository import AppConfigRepository
from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.external.llm.openai_llm import OpenAILLM
from app.domain.services.tools.a2a import A2AClientManager
from app.domain.services.tools.mcp import MCPClientManager
from app.interfaces.schemas.app_config import ListMCPServerItem, ListA2AServerItem

logger = logging.getLogger(__name__)


class AppConfigService:
    """应用配置服务"""

    def __init__(
            self,
            app_config_repository: AppConfigRepository,
            uow_factory: Optional[Callable[[], IUnitOfWork]] = None,
            user_id: Optional[str] = None,
    ) -> None:
        """构造函数，完成应用配置服务的初始化"""
        self.app_config_repository = app_config_repository
        self.uow_factory = uow_factory
        self.user_id = user_id

    async def _load_app_config(self) -> AppConfig:
        """加载获取所有的应用配置"""
        if self.user_id and self.uow_factory:
            async with self.uow_factory() as uow:
                user_config = await uow.user_config.get_by_user_id(self.user_id)

            if user_config:
                await self._migrate_agent_config(user_config)
                return user_config

            default_config = self.app_config_repository.load()
            await self._migrate_agent_config(default_config)
            async with self.uow_factory() as uow:
                await uow.user_config.save(self.user_id, default_config)

            return default_config

        app_config = self.app_config_repository.load()
        await self._migrate_agent_config(app_config)
        return app_config

    async def _migrate_agent_config(self, app_config: AppConfig) -> None:
        """兼容旧配置：此前单步骤迭代上限隐藏且默认 20，导致总上限 100 不生效。"""
        agent_config = app_config.agent_config
        if agent_config.max_iterations_per_step == 20 and agent_config.max_iterations > 20:
            agent_config.max_iterations_per_step = agent_config.max_iterations
            await self._save_app_config(app_config)

    async def get_app_config(self) -> AppConfig:
        """获取当前用户的完整应用配置。"""
        return await self._load_app_config()

    async def _save_app_config(self, app_config: AppConfig) -> None:
        """保存应用配置，登录用户优先写入数据库。"""
        if self.user_id and self.uow_factory:
            async with self.uow_factory() as uow:
                await uow.user_config.save(self.user_id, app_config)
            return

        self.app_config_repository.save(app_config)

    async def get_llm_config(self) -> LLMConfig:
        """获取LLM提供商配置"""
        app_config = await self._load_app_config()
        return app_config.llm_config

    async def update_llm_config(self, llm_config: LLMConfig) -> LLMConfig:
        """根据传递的llm_config更新语言模型提供商配置"""
        # 1.获取应用配置
        app_config = await self._load_app_config()

        # 2.判断api_key是否为空
        llm_config = self._normalize_llm_config(llm_config, app_config.llm_config)
        if not await self._test_llm_config(llm_config):
            raise BadRequestError("模型配置连接测试失败，未保存，请检查 base_url、api_key 和模型名")

        # 3.调用函数更新app_config
        app_config.llm_config = llm_config
        await self._save_app_config(app_config)

        return app_config.llm_config

    def _normalize_llm_config(self, llm_config: LLMConfig, current_llm_config: LLMConfig) -> LLMConfig:
        """补齐不从前端回传的敏感配置。"""
        if not llm_config.api_key.strip():
            llm_config.api_key = current_llm_config.api_key
        return llm_config

    async def _test_llm_config(self, llm_config: LLMConfig) -> bool:
        """用当前模型配置发起一次最小 LLM 调用。"""
        if not str(llm_config.base_url).strip() or not llm_config.api_key.strip() or not llm_config.model_name.strip():
            return False

        test_config = llm_config.model_copy(update={"max_tokens": min(llm_config.max_tokens or 64, 64)})
        llm = OpenAILLM(test_config, request_timeout=15)
        try:
            response = await llm.invoke([
                {"role": "user", "content": "Reply with OK."},
            ])
            return response.get("role") == "assistant" or bool(response.get("content") or response.get("tool_calls"))
        except Exception as e:
            logger.warning(f"LLM模型配置测试连接失败: {e}")
            return False

    async def test_llm_config(self, llm_config: LLMConfig) -> bool:
        """测试 LLM 配置，不保存。"""
        app_config = await self._load_app_config()
        llm_config = self._normalize_llm_config(llm_config, app_config.llm_config)
        return await self._test_llm_config(llm_config)

    async def get_agent_config(self) -> AgentConfig:
        """获取Agent通用配置"""
        app_config = await self._load_app_config()
        return app_config.agent_config

    async def update_agent_config(self, agent_config: AgentConfig) -> AgentConfig:
        """根据传递的agent_config更新Agent通用配置"""
        # 1.获取应用配置
        app_config = await self._load_app_config()

        # 2.调用函数更新app_config
        app_config.agent_config = agent_config
        await self._save_app_config(app_config)

        return app_config.agent_config

    async def get_mcp_servers(self) -> List[ListMCPServerItem]:
        """获取MCP服务器列表"""
        # 1.获取当前应用配置
        app_config = await self._load_app_config()

        mcp_servers = []
        for server_name, server_config in app_config.mcp_config.mcpServers.items():
            mcp_servers.append(ListMCPServerItem(
                server_name=server_name,
                enabled=server_config.enabled,
                available=server_config.available,
                transport=server_config.transport,
                tools=[],
            ))

        return mcp_servers

    async def test_mcp_server(self, server_name: str) -> bool:
        """测试 MCP 服务连接，通过后标记为可用于任务运行"""
        app_config = await self._load_app_config()
        if server_name not in app_config.mcp_config.mcpServers:
            raise NotFoundError(f"该MCP服务[{server_name}]不存在，请核实后重试")

        server_config = app_config.mcp_config.mcpServers[server_name]
        original_available = server_config.available
        server_config.available = True
        test_config = MCPConfig(mcpServers={server_name: server_config})
        manager = MCPClientManager(mcp_config=test_config)
        try:
            await manager.initialize()
            success = server_name in manager.tools
        except Exception as e:
            logger.warning(f"MCP服务[{server_name}]测试连接失败: {e}")
            success = False
        finally:
            await manager.cleanup()

        server_config.available = success
        if not success and original_available:
            server_config.available = False
        await self._save_app_config(app_config)
        return success

    async def update_and_create_mcp_servers(self, mcp_config: MCPConfig) -> MCPConfig:
        """根据传递的数据新增或更新MCP配置"""
        # 1.获取应用配置
        app_config = await self._load_app_config()

        # 2.使用新的mcp_config更新原始的配置
        app_config.mcp_config.mcpServers.update(mcp_config.mcpServers)

        # 3.调用数据仓库完成存储or更新
        await self._save_app_config(app_config)
        return app_config.mcp_config

    async def delete_mcp_server(self, server_name: str) -> MCPConfig:
        """根据名字删除MCP服务"""
        # 1.获取应用配置
        app_config = await self._load_app_config()

        # 2.查询对应服务的名字是否存在
        if server_name not in app_config.mcp_config.mcpServers:
            raise NotFoundError(f"该MCP服务[{server_name}]不存在，请核实后重试")

        # 3.如果存在则删除字典中对应的服务
        del app_config.mcp_config.mcpServers[server_name]
        await self._save_app_config(app_config)
        return app_config.mcp_config

    async def set_mcp_server_enabled(self, server_name: str, enabled: bool) -> MCPConfig:
        """更新MCP服务的启用状态"""
        # 1.获取应用配置
        app_config = await self._load_app_config()

        # 2.查询对应服务的名字是否存在
        if server_name not in app_config.mcp_config.mcpServers:
            raise NotFoundError(f"该MCP服务[{server_name}]不存在，请核实后重试")

        # 3.如果存在则更新该MCP服务的启用状态
        app_config.mcp_config.mcpServers[server_name].enabled = enabled
        await self._save_app_config(app_config)
        return app_config.mcp_config

    async def create_a2a_server(self, base_url: str) -> A2AConfig:
        """根据传递的配置新增a2a服务器"""
        # 1.获取当前的应用配置
        app_config = await self._load_app_config()

        # 2.往数据中新增a2a服务(在新增之前其实可以检测下当前Agent是否存在)
        a2a_server_config = A2AServerConfig(
            id=str(uuid.uuid4()),
            base_url=base_url,
            enabled=True,
            available=False,
        )
        app_config.a2a_config.a2a_servers.append(a2a_server_config)

        # 3.调用数据仓库更新
        await self._save_app_config(app_config)
        return app_config.a2a_config

    async def get_a2a_servers(self) -> List[ListA2AServerItem]:
        """获取A2A服务列表"""
        # 1.获取当前的应用配置
        app_config = await self._load_app_config()

        a2a_servers = []
        for server in app_config.a2a_config.a2a_servers:
            a2a_servers.append(ListA2AServerItem(
                id=server.id,
                base_url=server.base_url,
                name=server.base_url,
                description="",
                input_modes=[],
                output_modes=[],
                streaming=False,
                push_notifications=False,
                enabled=server.enabled,
                available=server.available,
            ))

        return a2a_servers

    async def test_a2a_server(self, a2a_id: str) -> bool:
        """测试 A2A 服务连接，通过后标记为可用于任务运行"""
        app_config = await self._load_app_config()
        target = None
        for server in app_config.a2a_config.a2a_servers:
            if server.id == a2a_id:
                target = server
                break
        if target is None:
            raise NotFoundError(f"该A2A服务[{a2a_id}]不存在，请核实后重试")

        target.available = True
        test_config = A2AConfig(a2a_servers=[target])
        manager = A2AClientManager(test_config)
        try:
            await manager.initialize()
            success = a2a_id in manager.agent_cards
        except Exception as e:
            logger.warning(f"A2A服务[{a2a_id}]测试连接失败: {e}")
            success = False
        finally:
            await manager.cleanup()

        target.available = success
        await self._save_app_config(app_config)
        return success

    async def set_a2a_server_enabled(self, a2a_id: str, enabled: bool) -> A2AConfig:
        """根据传递的id+enabled更新服务启用状态"""
        # 1.获取当前的应用配置
        app_config = await self._load_app_config()

        # 2.计算需要更新位置的索引并判断是否存在
        idx = None
        for item_idx, item in enumerate(app_config.a2a_config.a2a_servers):
            if item.id == a2a_id:
                idx = item_idx
                break
        if idx is None:
            raise NotFoundError(f"该A2A服务[{a2a_id}]不存在，请核实后重试")

        # 3.如果存在则更新数据
        app_config.a2a_config.a2a_servers[idx].enabled = enabled
        await self._save_app_config(app_config)
        return app_config.a2a_config

    async def delete_a2a_server(self, a2a_id: str) -> A2AConfig:
        """根据传递的id删除指定的a2a服务"""
        # 1.获取当前的应用配置
        app_config = await self._load_app_config()

        # 2.计算需要操作位置的索引并判断是否存在
        idx = None
        for item_idx, item in enumerate(app_config.a2a_config.a2a_servers):
            if item.id == a2a_id:
                idx = item_idx
                break
        if idx is None:
            raise NotFoundError(f"该A2A服务[{a2a_id}]不存在，请核实后重试")

        # 3.删除a2a服务器
        del app_config.a2a_config.a2a_servers[idx]
        await self._save_app_config(app_config)
        return app_config.a2a_config
