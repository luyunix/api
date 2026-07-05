# MCPTool 详解

`MCPTool` 用于把外部 MCP（Model Context Protocol）服务的能力暴露给 Agent。MCP 由 Anthropic 提出，是一种让 LLM 调用外部工具的标准协议。

---

## 1. 文件位置

`app/domain/services/tools/mcp.py`

---

## 2. 核心组件

```
MCPTool
  └── MCPClientManager
        ├── 连接多个 MCP Server
        ├── 缓存 ClientSession + Tool Schema
        └── 运行时调用 session.call_tool()
```

---

## 3. `MCPClientManager`

### 3.1 初始化

```python
class MCPClientManager:
    def __init__(self, mcp_config: Optional[MCPConfig] = None):
        self._mcp_config = mcp_config
        self._exit_stack = AsyncExitStack()
        self._clients = {}      # server_name -> ClientSession
        self._tools = {}        # server_name -> List[Tool]
        self._initialized = False
```

### 3.2 支持的传输协议

| 协议 | 说明 | 代码位置 |
|---|---|---|
| `stdio` | 启动本地子进程 | `mcp.py:105-147` |
| `sse` | SSE 流式 HTTP | `mcp.py:149-180` |
| `streamable_http` | 可流式 HTTP | `mcp.py:182-218` |

### 3.3 连接流程

```python
async def initialize(self) -> None:
    if self._initialized:
        return
    await self._connect_mcp_servers()
    self._initialized = True
```

对每个服务器：
1. 根据 `transport` 选择连接方式
2. 创建 `ClientSession`
3. 调用 `session.initialize()`
4. 缓存 session
5. 调用 `list_tools()` 缓存工具 schema

### 3.4 工具名处理

为避免不同 MCP 服务工具名冲突，系统自动加前缀：

```python
if server_name.startswith("mcp_"):
    tool_name = f"{server_name}_{tool.name}"
else:
    tool_name = f"mcp_{server_name}_{tool.name}"
```

例如：高德地图服务 `amap-maps-streamableHTTP` 中的 `geocode` 工具，最终名为 `mcp_amap-maps-streamableHTTP_geocode`。

### 3.5 调用流程

```python
async def invoke(self, tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
    # 1. 从 tool_name 反解 server_name + original_tool_name
    # 2. 取出对应 ClientSession
    # 3. result = await session.call_tool(original_tool_name, arguments)
    # 4. 把 result.content 拼接成字符串返回
```

---

## 4. `MCPTool`

```python
class MCPTool(BaseTool):
    name: str = "mcp"

    async def initialize(self, mcp_config: Optional[MCPConfig] = None) -> None:
        self._manager = MCPClientManager(mcp_config=mcp_config)
        await self._manager.initialize()
        self._tools = await self._manager.get_all_tools()
        self._initialized = True

    def get_tools(self) -> List[Dict[str, Any]]:
        return self._tools

    async def invoke(self, tool_name: str, **kwargs) -> ToolResult:
        return await self._manager.invoke(tool_name, kwargs)

    async def cleanup(self) -> None:
        if self._manager:
            await self._manager.cleanup()
```

`MCPTool` 本身不直接定义工具方法，而是把 `MCPClientManager` 加载的所有工具 schema 直接暴露出去。

---

## 5. 配置示例

当前 `config.yaml`：

```yaml
mcp_config:
  mcpServers:
    amap-maps-streamableHTTP:
      transport: streamable_http
      enabled: true
      url: https://mcp.amap.com/mcp?key=xxx
    jina-mcp-server:
      transport: streamable_http
      enabled: true
      url: https://mcp.jina.ai/v1
      headers:
        Authorization: Bearer xxx
```

---

## 6. 生命周期

| 阶段 | 方法 | 说明 |
|---|---|---|
| 初始化 | `initialize()` | 连接所有启用的 MCP 服务器 |
| 调用 | `invoke()` | 调用具体 MCP 工具 |
| 清理 | `cleanup()` | 关闭 AsyncExitStack，清理资源 |

**注意**：`cleanup()` 必须在初始化所在的同一个 asyncio Task 中调用，否则 anyio 可能抛出 `Attempted to exit cancel scope in a different task`。

---

## 7. 设计要点

- **动态扩展**：新增 MCP 服务只需改 `config.yaml`，无需改代码
- **协议无关**：支持 stdio/sse/streamable_http 三种传输
- **名字隔离**：自动前缀避免工具名冲突
- **结果归一化**：把 MCP 结果统一转成 `ToolResult`

---

*文档生成时间：2026-06-14*
