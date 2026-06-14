# FileTool 详解

`FileTool` 提供在 Docker 沙箱中读写、搜索、查找文件的能力，是 Agent 查看代码、修改配置、分析日志的主要工具。

---

## 1. 文件位置

`app/domain/services/tools/file.py`

---

## 2. 类定义

```python
class FileTool(BaseTool):
    """文件工具箱"""
    name: str = "file"

    def __init__(self, sandbox: Sandbox) -> None:
        super().__init__()
        self.sandbox = sandbox
```

- 继承 `BaseTool`
- 依赖 `Sandbox` 接口，所有文件操作在 Docker 沙箱中执行

---

## 3. 工具清单

### 3.1 `read_file`

| 属性 | 值 |
|---|---|
| 作用 | 读取文件内容 |
| 参数 | `filepath`, `start_line`, `end_line`, `sudo`, `max_length` |
| 必需 | `filepath` |

```python
@tool(
    name="read_file",
    description="读取文件内容。用于检查文件内容、分析日志或读取配置文件。",
    parameters={
        "filepath": {"type": "string", "description": "要读取文件的绝对路径"},
        "start_line": {"type": "integer", "description": "(可选)读取的起始行, 索引从 0 开始"},
        "end_line": {"type": "integer", "description": "(可选)结束行号, 不包含该行"},
        "sudo": {"type": "boolean", "description": "(可选)是否使用 sudo 权限"},
        "max_length": {"type": "integer", "description": "(可选)读取文件内容的最大长度, 默认为10000"},
    },
    required=["filepath"],
)
async def read_file(self, filepath, start_line=None, end_line=None, sudo=False, max_length=10000) -> ToolResult:
    return await self.sandbox.read_file(...)
```

### 3.2 `write_file`

| 属性 | 值 |
|---|---|
| 作用 | 写入或追加文件内容 |
| 参数 | `filepath`, `content`, `append`, `leading_newline`, `trailing_newline`, `sudo` |
| 必需 | `filepath`, `content` |

```python
async def write_file(self, filepath, content, append=False, leading_newline=False, trailing_newline=False, sudo=False) -> ToolResult:
    return await self.sandbox.write_file(...)
```

### 3.3 `replace_in_file`

| 属性 | 值 |
|---|---|
| 作用 | 在文件中替换指定字符串 |
| 参数 | `filepath`, `old_str`, `new_str`, `sudo` |
| 必需 | `filepath`, `old_str`, `new_str` |

```python
async def replace_in_file(self, filepath, old_str, new_str, sudo=False) -> ToolResult:
    return await self.sandbox.replace_in_file(...)
```

### 3.4 `search_in_file`

| 属性 | 值 |
|---|---|
| 作用 | 在文件内容中搜索匹配文本 |
| 参数 | `filepath`, `regex`, `sudo` |
| 必需 | `filepath`, `regex` |

```python
async def search_in_file(self, filepath, regex, sudo=False) -> ToolResult:
    return await self.sandbox.search_in_file(...)
```

### 3.5 `find_files`

| 属性 | 值 |
|---|---|
| 作用 | 按 glob 模式查找文件 |
| 参数 | `dir_path`, `glob_pattern` |
| 必需 | `dir_path`, `glob_pattern` |

```python
async def find_files(self, dir_path, glob_pattern) -> ToolResult:
    return await self.sandbox.find_files(...)
```

---

## 4. 运行环境

所有文件操作都在 `faber-sandbox` Docker 容器内进行，与宿主机隔离。

沙箱实现：`/Users/lyn/Desktop/faber/api/app/infrastructure/external/sandbox/docker_sandbox.py`

---

## 5. 典型使用流程

1. **查找文件**：`find_files(dir_path="/workspace", glob_pattern="*.py")`
2. **读取文件**：`read_file(filepath="/workspace/main.py")`
3. **替换内容**：`replace_in_file(filepath="/workspace/main.py", old_str="old", new_str="new")`
4. **搜索内容**：`search_in_file(filepath="/workspace/main.py", regex="def .*")`
5. **写入新文件**：`write_file(filepath="/workspace/new.py", content="...")`

---

## 6. 设计要点

- **沙箱隔离**：文件系统与宿主机隔离，防止误操作
- **防止上下文爆炸**：`read_file` 默认 `max_length=10000`，避免把大文件全部读进 LLM 上下文
- **代码修改友好**：`replace_in_file` 是 Agent 修改代码的主要方式
- **sudo 支持**：可以读取/修改需要 root 权限的文件

---

## 7. 遗留文件说明

同目录下还有一个 `app/domain/services/tools/tool.py`，里面也有一个 `FileTool`，但方法名不同（`file_read/file_write/file_str_replace/...`）。**该文件没有被任何流程引用**，实际使用的是 `file.py` 中的版本。它可能是旧实现或重构中间产物。

---

*文档生成时间：2026-06-14*
