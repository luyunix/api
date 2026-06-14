# A2ATool 详解

`A2ATool` 用于调用远程 Agent（Agent-to-Agent）。A2A 是 Google 提出的协议，允许一个 Agent 把任务委托给另一个远程 Agent。

---

## 1. 文件位置

`app/domain/services/tools/a2a.py`

---

## 2. 核心组件

```
A2ATool
  └── A2AClientManager
        ├── httpx.AsyncClient
        ├── 缓存远程 Agent 的 agent-card.json
        └── 调用远程 Agent endpoint
```

---

## 3. `A2AClientManager`

### 3.1 初始化

```python
class A2AClientManager:
    def __init__(self, a2a_config: Optional[A2AConfig] = None):
        self._a2a_config = a2a_config
        self._exit_stack = AsyncExitStack()
        self._httpx_client = None
        self._agent_cards = {}
        self._initialized = False
```

### 3.2 Agent Card 加载

```python
async def _get_a2a_agent_cards(self) -> None:
    for server in self._a2a_config.a2a_servers:
        response = await self._httpx_client.get(
            f"{server.base_url}/.well-known/agent-card.json"
        )
        agent_card = response.json()
        agent_card["enabled"] = server.enabled
        self._agent_cards[server.id] = agent_card
```

Agent Card 包含远程 Agent 的 id、名称、描述、技能、调用端点等信息。

### 3.3 远程调用

```python
async def invoke(self, agent_id: str, query: str) -> ToolResult:
    agent_card = self._agent_cards.get(agent_id)
    url = agent_card.get("url", "")

    response = await self._httpx_client.post(
        url,
        json={
            "id": str(uuid.uuid4()),
            "jsonrpc": "2.0",
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": str(uuid.uuid4()),
                    "role": "user",
                    "parts": [{"kind": "text", "text": query}],
                }
            }
        }
    )
    return ToolResult(success=True, data=response.json())
```

请求采用 JSON-RPC 2.0 格式，方法为 `message/send`。

---

## 4. `A2ATool`

```python
class A2ATool(BaseTool):
    name: str = "a2a"

    async def initialize(self, a2a_config: Optional[A2AConfig] = None) -> None:
        self.manager = A2AClientManager(a2a_config)
        await self.manager.initialize()
        self._initialized = True
```

### 4.1 工具清单

#### `get_remote_agent_cards`

| 属性 | 值 |
|---|---|
| 作用 | 获取所有已配置远程 Agent 的卡片信息 |
| 参数 | 无 |
| 必需 | 无 |

```python
@tool(
    name="get_remote_agent_cards",
    description="获取可远程调用的Agent卡片信息, 包含Agent id、名称、描述、技能、请求端点等。",
    parameters={},
    required=[]
)
async def get_remote_agent_cards(self) -> ToolResult:
    agent_cards = []
    for id, agent_card in self.manager.agent_cards.items():
        agent_cards.append({"id": id, **agent_card})
    return ToolResult(success=True, data=agent_cards)
```

#### `call_remote_agent`

| 属性 | 值 |
|---|---|
| 作用 | 调用指定远程 Agent |
| 参数 | `id`, `query` |
| 必需 | `id`, `query` |

```python
@tool(
    name="call_remote_agent",
    description="根据传递的id+query(分配给远程Agent完成的任务query)调用远程Agent完成对应需求",
    parameters={
        "id": {"type": "string", "description": "需要调用远程agent的id"},
        "query": {"type": "string", "description": "需要分配给该远程Agent实现的任务/需求query"},
    },
    required=["id", "query"],
)
async def call_remote_agent(self, id: str, query: str) -> ToolResult:
    return await self.manager.invoke(agent_id=id, query=query)
```

---

## 5. 配置

当前 `config.yaml` 中 A2A 配置为空：

```yaml
a2a_config:
  a2a_servers: []
```

因此当前没有任何远程 Agent 可用。如需启用，需要添加 A2A 服务器配置。

---

## 6. 典型使用流程

1. **获取可用 Agent**：`get_remote_agent_cards()`
2. **调用远程 Agent**：`call_remote_agent(id="agent-1", query="帮我把这段文字翻译成英文")`
3. **处理返回结果**：远程 Agent 的结果作为 `ToolResult.data` 返回

---

## 7. 设计要点

- **标准协议**：使用 A2A 标准的 `agent-card.json` + `message/send`
- **动态发现**：运行时从远程服务获取 Agent 能力描述
- **JSON-RPC 2.0**：请求格式符合 A2A 规范
- **当前未启用**：`config.yaml` 中 `a2a_servers` 为空

---

*文档生成时间：2026-06-14*
