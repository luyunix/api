# MessageTool 详解

`MessageTool` 是 Agent 与用户交互的工具，用于发送通知、请求澄清、收集额外信息。

---

## 1. 文件位置

`app/domain/services/tools/message.py`

---

## 2. 类定义

```python
class MessageTool(BaseTool):
    """消息工具，用于完成消息工具初始化"""
    name: str = "message"

    def __init__(self) -> None:
        super().__init__()
```

- 继承 `BaseTool`
- 不依赖沙箱或外部服务，纯逻辑工具

---

## 3. 工具清单

### 3.1 `message_notify_user`

| 属性 | 值 |
|---|---|
| 作用 | 向用户发送通知，无需回复 |
| 参数 | `text` |
| 必需 | `text` |

```python
@tool(
    name="message_notify_user",
    description="向用户发送消息，且无需用户回复。用于确认收到消息、提供进度更新、报告任务完成情况，或解释处理方式的变更。",
    parameters={
        "text": {"type": "string", "description": "要显示给用户的消息文本"},
    },
    required=["text"]
)
async def message_notify_user(self, text: str) -> ToolResult:
    return ToolResult(success=True, data="Continue")
```

### 3.2 `message_ask_user`

| 属性 | 值 |
|---|---|
| 作用 | 向用户提问并等待回复 |
| 参数 | `text`, `attachments`, `suggest_user_takeover` |
| 必需 | `text` |

```python
@tool(
    name="message_ask_user",
    description="向用户提问并等待回复。用于：请求澄清、寻求确认、或收集额外信息。",
    parameters={
        "text": {"type": "string", "description": "要展示给用户的问题文本"},
        "attachments": {
            "anyOf": [
                {"type": "string"},
                {"items": {"type": "string"}, "type": "array"},
            ],
            "description": "(可选)与问题相关的文件或参考资料",
        },
        "suggest_user_takeover": {
            "type": "string",
            "enum": ["none", "browser"],
            "description": "(可选)建议用户接管的操作（例如由用户在浏览器中手动完成某些事）。"
        },
    },
    required=["text"],
)
async def message_ask_user(self, text, attachments=None, suggest_user_takeover=None) -> ToolResult:
    return ToolResult(success=True)
```

---

## 4. 与用户交互的实现

`MessageTool` 本身只返回 `ToolResult(success=True)`，真正的通知/等待逻辑由上层事件流承载：

1. `BaseAgent.invoke()` 发出 `ToolEvent(status=CALLING)`
2. `AgentTaskRunner` 把事件持久化并放入输出流
3. 前端通过 SSE 接收到事件，展示消息或弹窗
4. 用户回复后，新的消息进入下一轮 `Agent.invoke()`

---

## 5. `suggest_user_takeover` 字段

当 `suggest_user_takeover="browser"` 时，前端可以提示用户接管浏览器进行手动操作。这是自动化无法完成某些步骤时的兜底方案。

---

## 6. 设计要点

- **异步交互**：`ask_user` 不是阻塞等待，而是通过事件流实现跨轮次回复
- **纯标记工具**：方法本身不执行网络或文件操作，只生成事件信号
- **前端驱动**：具体 UI 表现由前端根据事件决定

---

*文档生成时间：2026-06-14*
