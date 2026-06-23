import uuid
from enum import Enum
from typing import Dict, Optional, List, Any

from pydantic import BaseModel, HttpUrl, Field, ConfigDict, model_validator


class LLMConfig(BaseModel):
    """LLM提供商配置"""
    base_url: HttpUrl = "https://api.deepseek.com"  # 模型基础URL地址
    api_key: str = ""  # 模型API秘钥
    model_name: str = "deepseek-reasoner"  # 模型名字，默认使用deepseek-reasoner带推理的模型，传递tools会自动切换到deepseek-chat
    temperature: float = Field(0.7)  # 温度，默认设置为0.7
    max_tokens: int = Field(8192, ge=0)  # 最大输出token数，默认设置为deepseek-chat模型的最大输出限制


class EmbeddingConfig(BaseModel):
    """Embedding提供商配置

    独立于 LLM 配置：DeepSeek 不提供 embedding 接口，需要单独配置一个
    OpenAI 兼容的 embedding provider（DashScope/Qwen、SiliconFlow、OpenAI 等）。
    dimension 必须与 pgvector 列维度（EpisodicMemoryModel.EMBEDDING_DIMENSION）一致，
    修改 dimension 需新建迁移并重新生成向量。
    """
    enabled: bool = False  # 是否启用情景记忆（需要 pgvector + embedding provider）
    base_url: HttpUrl = "https://dashscope.aliyuncs.com/compatible-mode/v1"  # 默认DashScope(Qwen)
    api_key: str = ""  # Embedding API秘钥（DashScope: DASHSCOPE_API_KEY）
    model_name: str = "text-embedding-v3"  # 默认DashScope Qwen，1024维
    dimension: int = Field(1024, gt=0)  # 向量维度，需与pgvector列维度一致
    batch_size: int = Field(32, gt=0)  # 批量生成向量时的批次大小


class AgentConfig(BaseModel):
    """Agent通用配置"""
    max_iterations: int = Field(default=100, gt=0, lt=1000)  # Agent最大迭代次数
    max_iterations_per_step: int = Field(default=20, gt=0, lt=100)  # ReAct单步最大迭代次数
    max_retries: int = Field(default=3, gt=1, lt=10)  # 最大重试次数
    max_search_results: int = Field(default=10, gt=1, lt=30)  # 最大搜索结果条数
    reflection_interval: int = Field(default=5, ge=0, lt=100)  # ReAct工具循环反思间隔，0表示关闭
    task_timeout_seconds: int = Field(default=600, gt=0, lt=3600)  # 任务级超时时间（秒）
    enable_early_completion: bool = Field(default=True)  # 是否启用提前完成检测
    context_window: int = Field(default=65536, gt=0)  # 模型上下文窗口token数（DeepSeek ~64K），用于记忆预算计算


class MCPTransport(str, Enum):
    """MCP传输类型枚举"""
    STDIO = "stdio"  # 本地输入输出
    SSE = "sse"  # 流式事件
    STREAMABLE_HTTP = "streamable_http"  # 流式HTTP


class MCPServerConfig(BaseModel):
    """MCP服务配置"""
    # 通用配置字段
    transport: MCPTransport = MCPTransport.STREAMABLE_HTTP  # 传输协议
    enabled: bool = True  # 是否开启，默认为True
    description: Optional[str] = None  # 服务器描述
    env: Optional[Dict[str, Any]] = None  # 环境变量配置

    # stdio配置
    command: Optional[str] = None  # 启用命令
    args: Optional[List[str]] = None  # 命令参数

    # streamable_http&sse配置
    url: Optional[str] = None  # MCP服务URL地址
    headers: Optional[Dict[str, Any]] = None  # MCP服务请求头

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def validate_mcp_server_config(self):
        """校验mcp_server_config的相关信息，包含url+command"""
        # 1.判断transport是否为sse/streamable_http
        if self.transport in [MCPTransport.SSE, MCPTransport.STREAMABLE_HTTP]:
            # 2.这两种模式需要传递url
            if not self.url:
                raise ValueError("在sse或streamable_http模式下必须传递url")

        # 3.判断transport是否为stdio类型
        if self.transport == MCPTransport.STDIO:
            # 4.stdio类型必须传递command
            if not self.command:
                raise ValueError("在stdio模式下必须传递command")

        return self


class MCPConfig(BaseModel):
    """应用MCP配置"""
    mcpServers: Dict[str, MCPServerConfig] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)


class A2AServerConfig(BaseModel):
    """A2A服务配置"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))  # 唯一标识
    base_url: str  # 服务基础URL
    enabled: bool = True  # 服务是否开启


class A2AConfig(BaseModel):
    """A2A配置"""
    a2a_servers: List[A2AServerConfig] = Field(default_factory=list)


class AppConfig(BaseModel):
    """应用配置信息，包含Agent配置、LLM提供商配置、Embedding配置、MCP配置、A2A配置"""
    llm_config: LLMConfig  # 语言模型配置
    agent_config: AgentConfig  # Agent通用配置
    embedding_config: EmbeddingConfig = Field(default_factory=EmbeddingConfig)  # Embedding提供商配置
    mcp_config: MCPConfig  # MCP服务配置
    a2a_config: A2AConfig  # A2A服务配置

    # Pydantic配置，允许传递额外的字段初始化
    model_config = ConfigDict(extra="allow")
