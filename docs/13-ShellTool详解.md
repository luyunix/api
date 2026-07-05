# ShellTool 详解

`ShellTool` 提供在 Docker 沙箱中执行 Shell 命令的能力，是 Agent 运行代码、安装依赖、管理文件的核心工具。

---

## 1. 文件位置

`app/domain/services/tools/shell.py`

---

## 2. 类定义

```python
class ShellTool(BaseTool):
    """Shell工具箱，提供Shell交互相关功能"""
    name: str = "shell"

    def __init__(self, sandbox: Sandbox) -> None:
        super().__init__()
        self.sandbox = sandbox
```

- 继承 `BaseTool`
- 依赖 `Sandbox` 接口，所有命令实际在 Docker 沙箱中执行

---

## 3. 工具清单

### 3.1 `shell_execute`

| 属性 | 值 |
|---|---|
| 作用 | 在指定 Shell 会话中执行命令 |
| 参数 | `session_id`, `exec_dir`, `command` |
| 必需 | `session_id`, `exec_dir`, `command` |

```python
@tool(
    name="shell_execute",
    description="在指定 Shell 会话中执行命令。可用于运行代码、安装依赖包或文件管理。",
    parameters={
        "session_id": {"type": "string", "description": "目标 Shell 会话的唯一标识符"},
        "exec_dir": {"type": "string", "description": "执行命令的工作目录（必须使用绝对路径）"},
        "command": {"type": "string", "description": "要执行的 Shell 命令"},
    },
    required=["session_id", "exec_dir", "command"],
)
async def shell_execute(self, session_id: str, exec_dir: str, command: str) -> ToolResult:
    return await self.sandbox.exec_command(session_id, exec_dir, command)
```

### 3.2 `shell_read_output`

| 属性 | 值 |
|---|---|
| 作用 | 查看指定 Shell 会话的输出 |
| 参数 | `session_id` |
| 必需 | `session_id` |

```python
async def shell_read_output(self, session_id: str) -> ToolResult:
    return await self.sandbox.read_shell_output(session_id)
```

### 3.3 `shell_wait_process`

| 属性 | 值 |
|---|---|
| 作用 | 等待指定 Shell 会话中正在运行的进程返回 |
| 参数 | `session_id`, `seconds` |
| 必需 | `session_id` |

```python
async def shell_wait_process(self, session_id: str, seconds: Optional[int] = None) -> ToolResult:
    return await self.sandbox.wait_process(session_id, seconds)
```

### 3.4 `shell_write_input`

| 属性 | 值 |
|---|---|
| 作用 | 向交互式命令提示符写入输入 |
| 参数 | `session_id`, `input_text`, `press_enter` |
| 必需 | `session_id`, `input_text`, `press_enter` |

```python
async def shell_write_input(self, session_id: str, input_text: str, press_enter: str) -> ToolResult:
    return await self.sandbox.write_shell_input(session_id, input_text, press_enter)
```

### 3.5 `shell_kill_process`

| 属性 | 值 |
|---|---|
| 作用 | 终止指定 Shell 会话中的进程 |
| 参数 | `session_id` |
| 必需 | `session_id` |

```python
async def shell_kill_process(self, session_id: str) -> ToolResult:
    return await self.sandbox.kill_process(session_id)
```

---

## 4. 运行环境

所有 Shell 命令都在 `faber-sandbox` Docker 容器内执行，与宿主机隔离。

沙箱镜像：`/Users/lyn/Desktop/faber/sandbox/Dockerfile`
沙箱实现：`/Users/lyn/Desktop/faber/api/app/infrastructure/external/sandbox/docker_sandbox.py`

沙箱内默认用户是 `ubuntu`，拥有 sudo 权限。

---

## 5. 典型使用流程

1. **执行命令**：`shell_execute(session_id="s1", exec_dir="/workspace", command="python3 main.py")`
2. **查看输出**：`shell_read_output(session_id="s1")`
3. **等待完成**：`shell_wait_process(session_id="s1", seconds=10)`
4. **交互输入**：`shell_write_input(session_id="s1", input_text="yes", press_enter=True)`
5. **强制终止**：`shell_kill_process(session_id="s1")`

---

## 6. 设计要点

- **会话隔离**：每个 `session_id` 对应独立的 Shell 会话，长任务不会互相污染
- **交互式支持**：`write_input` 和 `read_output` 支持 `python`、交互式安装脚本等场景
- **工作目录控制**：`exec_dir` 必须是绝对路径，确保命令在预期位置执行
- **超时可控**：`wait_process` 可以指定等待秒数

---

*文档生成时间：2026-06-14*
