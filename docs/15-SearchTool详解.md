# SearchTool 详解

`SearchTool` 提供网页搜索能力，用于获取实时信息、补充内部知识、进行事实核查。

---

## 1. 文件位置

`app/domain/services/tools/search.py`

---

## 2. 类定义

```python
class SearchTool(BaseTool):
    """搜索工具包，提供与搜索引擎交互的能力"""
    name: str = "search"

    def __init__(self, search_engine: SearchEngine) -> None:
        super().__init__()
        self.search_engine = search_engine
```

- 继承 `BaseTool`
- 依赖 `SearchEngine` 接口，具体搜索实现可切换

---

## 3. 工具清单

### 3.1 `search_web`

| 属性 | 值 |
|---|---|
| 作用 | 全网搜索引擎 |
| 参数 | `query`, `date_range` |
| 必需 | `query` |

```python
@tool(
    name="search_web",
    description="全网搜索引擎工具。当需要获取实时信息（如突发新闻、天气）、补充内部知识库未涵盖的内容或进行事实核查时使用。",
    parameters={
        "query": {
            "type": "string",
            "description": "针对搜索引擎优化的查询字符串。请提取问题中的核心实体和关键词（3-5个），避免使用完整的自然语言问句（例如将'今天北京的天气怎么样'转换为'北京 天气'）。"
        },
        "date_range": {
            "type": "string",
            "enum": ["all", "past_hour", "past_day", "past_week", "past_month", "past_year"],
            "description": "（可选）搜索结果的时间范围过滤。当用户询问特定时效性的新闻或事件时（如'昨天'、'上周'），必须指定此参数。默认为 'all'。"
        }
    },
    required=["query"]
)
async def search_web(self, query: str, date_range: Optional[str] = None) -> ToolResult[SearchResults]:
    return await self.search_engine.invoke(query, date_range)
```

---

## 4. 查询优化

工具描述中明确要求 LLM 把自然语言问题转换成关键词形式：

- 输入：`今天北京的天气怎么样？`
- 优化后：`北京 天气`

这是为了减少搜索噪声，提高结果相关性。

---

## 5. 时间过滤

`date_range` 支持以下取值：

| 值 | 含义 |
|---|---|
| `all` | 全部时间 |
| `past_hour` | 过去一小时 |
| `past_day` | 过去一天 |
| `past_week` | 过去一周 |
| `past_month` | 过去一月 |
| `past_year` | 过去一年 |

时效性强的查询（新闻、股价、天气等）应该指定该参数。

---

## 6. 返回结构

文件：`app/domain/models/search.py`

```python
class SearchResults(BaseModel):
    results: List[SearchResult]

class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
```

搜索结果包含标题、链接和摘要，LLM 可以基于这些信息继续决策。

---

## 7. 设计要点

- **可插拔实现**：通过 `SearchEngine` 接口，底层可以切换不同搜索提供商
- **关键词优化**：提示词引导 LLM 生成更短的搜索关键词
- **时效性支持**：`date_range` 过滤适合新闻、股票等场景
- **结果摘要**：返回结构化摘要，控制上下文长度

---

*文档生成时间：2026-06-14*
