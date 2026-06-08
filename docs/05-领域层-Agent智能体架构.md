# Faber API —— 领域层（Domain）：Agent 智能体架构

这是 Faber **最核心、最精彩**的部分。整个系统的"智能"都来源于此。

Faber 采用 **Multi-Agent 架构**：两个专门的 Agent 协同工作：
- **PlannerAgent（规划者）**：负责"思考"，将用户需求拆解为可执行的步骤列表
- **ReActAgent（执行者）**：负责"动手"，使用工具逐步完成每个步骤

两者由 **PlannerReActFlow（流程状态机）** 统一调度。

---

## 1. 架构全景

```
┌─────────────────────────────────────────────────────────────┐
│                    PlannerReActFlow                          │
│                   （流程状态机）                               │
│                                                              │
│   IDLE → PLANNING → EXECUTING → UPDATING → EXECUTING → ... │
│                         ↓                                    │
│                    SUMMARIZING → COMPLETED                   │
└─────────────────────────────────────────────────────────────┘
          │                      │
          ▼                      ▼
┌─────────────────┐    ┌─────────────────┐
│  PlannerAgent   │    │   ReActAgent    │
│   （规划者）      │    │   （执行者）      │
│                 │    │                 │
│  create_plan()  │    │ execute_step()  │
│  update_plan()  │    │   summarize()   │
└─────────────────┘    └─────────────────┘
          │                      │
          └──────────┬───────────┘
                     ▼
            ┌─────────────────┐
            │   BaseAgent     │
            │  （通用基类）     │
            │                 │
            │  invoke()       │
            │  _invoke_llm()  │
            │  _invoke_tool() │
            │  _add_to_memory()│
            └─────────────────┘
                     │
                     ▼
            ┌─────────────────┐
            │      LLM        │
            │  （DeepSeek/    │
            │   OpenAI 等）   │
            └─────────────────┘
```

---

## 2. BaseAgent：通用智能体基类

`BaseAgent` 是所有 Agent 的抽象基类，实现了 LLM 调用、工具调用、记忆管理的通用逻辑。

### 2.1 构造函数

```python
class BaseAgent(ABC):
    name: str = ""
    _system_prompt: str = ""           # 系统预设提示词
    _format: Optional[str] = None      # 响应格式（如 "json_object"）
    _retry_interval: float = 1.0       # 重试间隔
    _tool_choice: Optional[str] = None # 强制选择工具策略
    
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        session_id: str,
        agent_config: AgentConfig,
        llm: LLM,
        json_parser: JSONParser,
        tools: List[BaseTool],
    ):
        self._uow_factory = uow_factory
        self._uow = uow_factory()
        self._session_id = session_id
        self._agent_config = agent_config
        self._llm = llm
        self._memory: Optional[Memory] = None
        self._json_parser = json_parser
        self._tools = tools
```

### 2.2 invoke()：Agent 的主入口

```python
async def invoke(self, query: str, format: Optional[str] = None) -> AsyncGenerator[BaseEvent, None]:
    format = format if format else self._format
    
    # 1. 调用 LLM 获取初始响应
    message = await self._invoke_llm(
        [{"role": "user", "content": query}],
        format,
    )
    
    # 2. ReAct 循环：最多 max_iterations 次工具调用
    for _ in range(self._agent_config.max_iterations):
        # 如果没有工具调用，说明 LLM 直接给出了文本回答
        if not message or not message.get("tool_calls"):
            break
        
        # 3. 执行工具调用
        tool_messages = []
        for tool_call in message["tool_calls"]:
            tool_call_id = tool_call["id"] or str(uuid.uuid4())
            function_name = tool_call["function"]["name"]
            function_args = await self._json_parser.invoke(tool_call["function"]["arguments"])
            
            tool = self._get_tool(function_name)
            
            # 4. 发送 "工具即将调用" 事件（前端可显示"正在搜索..."）
            yield ToolEvent(
                tool_call_id=tool_call_id,
                tool_name=tool.name,
                function_name=function_name,
                function_args=function_args,
                status=ToolEventStatus.CALLING,
            )
            
            # 5. 真正调用工具
            result = await self._invoke_tool(tool, function_name, function_args)
            
            # 6. 发送 "工具调用完成" 事件（前端可显示搜索结果）
            yield ToolEvent(
                tool_call_id=tool_call_id,
                tool_name=tool.name,
                function_name=function_name,
                function_args=function_args,
                function_result=result,
                status=ToolEventStatus.CALLED,
            )
            
            # 7. 组装工具响应，喂回 LLM
            tool_messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "function_name": function_name,
                "content": result.model_dump_json(),
            })
        
        # 8. 将工具结果喂回 LLM，让它继续思考
        message = await self._invoke_llm(tool_messages)
    else:
        # 超过最大迭代次数
        yield ErrorEvent(error=f"Agent迭代超过最大迭代次数: {self._agent_config.max_iterations}")
    
    # 9. 返回最终文本回答
    if message and message.get("content") is not None:
        yield MessageEvent(message=message["content"])
    else:
        yield ErrorEvent(error="Agent未能生成有效回复内容")
```

### 2.3 _invoke_llm()：带重试的 LLM 调用

```python
async def _invoke_llm(self, messages: List[Dict], format: Optional[str] = None) -> Dict[str, Any]:
    # 1. 将消息添加到记忆
    await self._add_to_memory(messages)
    
    response_format = {"type": format} if format else None
    error = "调用语言模型发生错误"
    
    # 2. 重试循环
    for _ in range(self._agent_config.max_retries):
        try:
            message = await self._llm.invoke(
                messages=self._memory.get_messages(),
                tools=self._get_available_tools(),
                response_format=response_format,
                tool_choice=self._tool_choice,
            )
            
            # 3. 处理空回复（兼容 DeepSeek 思考模型）
            if message.get("role") == "assistant":
                if not message.get("content") and not message.get("tool_calls"):
                    logger.warning("LLM回复了空内容，执行重试")
                    await self._add_to_memory([
                        {"role": "assistant", "content": ""},
                        {"role": "user", "content": "AI无响应内容，请继续。"}
                    ])
                    await asyncio.sleep(self._retry_interval)
                    continue
                
                # 4. 过滤消息（保留 content、reasoning_content、tool_calls）
                filtered_message = {"role": "assistant", "content": message.get("content")}
                if message.get("reasoning_content"):
                    filtered_message["reasoning_content"] = message["reasoning_content"]
                if message.get("tool_calls"):
                    filtered_message["tool_calls"] = message["tool_calls"][:1]  # 限制一次只调一个工具
            
            # 5. 保存到记忆并返回
            await self._add_to_memory([filtered_message])
            return filtered_message
            
        except Exception as e:
            logger.error(f"调用语言模型发生错误: {e}")
            error = str(e)
            await asyncio.sleep(self._retry_interval)
            continue
    
    raise RuntimeError(f"调用语言模型失败, 已达到最大重试次数: {error}")
```

**关键设计点**：
- **限制一次只调用一个工具**（`tool_calls[:1]`）：降低复杂度，避免并行工具调用的结果混乱
- **处理空回复**：某些模型（尤其是思考模型）可能先返回空 content，需要自动重试
- **保存 reasoning_content**：DeepSeek 等模型的思考过程，虽然对 LLM 后续推理无用，但需要保留给前端展示

### 2.4 _invoke_tool()：带重试的工具调用

```python
async def _invoke_tool(self, tool: BaseTool, tool_name: str, arguments: Dict) -> ToolResult:
    err = ""
    for _ in range(self._agent_config.max_retries):
        try:
            return await tool.invoke(tool_name, **arguments)
        except Exception as e:
            err = str(e)
            logger.exception(f"调用工具[{tool_name}]出错")
            await asyncio.sleep(self._retry_interval)
            continue
    
    # 最大重试后仍失败，将错误作为结果返回给 LLM，让它自行处理
    return ToolResult(success=False, message=err)
```

**容错设计**：工具调用失败不会直接导致任务失败，而是将错误信息告诉 LLM，LLM 可以决定重试、换工具或告知用户。

### 2.5 记忆管理

```python
async def _add_to_memory(self, messages: List[Dict]) -> None:
    await self._ensure_memory()
    
    # 空记忆时添加系统提示词
    if self._memory.empty:
        self._memory.add_message({"role": "system", "content": self._system_prompt})
    
    self._memory.add_messages(messages)
    
    # 持久化到数据库
    async with self._uow:
        await self._uow.session.save_memory(self._session_id, self.name, self._memory)
```

### 2.6 roll_back()：状态回滚

当用户在中途发送新消息（打断当前任务）时，需要确保 Agent 的消息列表格式正确：

```python
async def roll_back(self, message: Message) -> None:
    await self._ensure_memory()
    last_message = self._memory.get_last_message()
    
    # 如果最后一条是工具调用，且不是 ask_user，则删除它
    if last_message and last_message.get("tool_calls"):
        tool_call = last_message["tool_calls"][0]
        function_name = tool_call["function"]["name"]
        
        if function_name == "message_ask_user":
            # ask_user 需要保留，将用户的新消息作为工具响应
            self._memory.add_message({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": message.model_dump_json(),
            })
        else:
            # 其他工具调用直接删除
            self._memory.roll_back()
    
    async with self._uow:
        await self._uow.session.save_memory(self._session_id, self.name, self._memory)
```

---

## 3. PlannerAgent：规划智能体

PlannerAgent 的职责是"做计划"。它不做具体的工具调用，只输出结构化的计划。

### 3.1 配置

```python
class PlannerAgent(BaseAgent):
    name: str = "planner"
    _system_prompt: str = SYSTEM_PROMPT + PLANNER_SYSTEM_PROMPT
    _format: Optional[str] = "json_object"   # 强制返回 JSON
    _tool_choice: Optional[str] = "none"     # 不允许调用工具
```

- `_format = "json_object"`：要求 LLM 返回合法 JSON，便于解析为 Plan 对象
- `_tool_choice = "none"`：Planner 只思考不执行，不需要工具

### 3.2 create_plan()：创建计划

```python
async def create_plan(self, message: Message) -> AsyncGenerator[BaseEvent, None]:
    query = CREATE_PLAN_PROMPT.format(
        message=message.message,
        attachments="\n".join(message.attachments),
    )
    
    async for event in self.invoke(query):
        if isinstance(event, MessageEvent):
            # LLM 返回的是 JSON 字符串
            parsed_obj = await self._json_parser.invoke(event.message)
            plan = Plan.model_validate(parsed_obj)
            yield PlanEvent(plan=plan, status=PlanEventStatus.CREATED)
        else:
            yield event
```

**CREATE_PLAN_PROMPT 的核心要求**：

```
你现在正在根据用户的消息创建一个计划:
{message}

返回格式要求：
- 必须返回符合以下 TypeScript 接口定义的 JSON 格式

interface CreatePlanResponse {
  message: string;      // 对用户的回复和思考
  language: string;     // 工作语言（如 "zh"）
  steps: Array<{id: string, description: string}>;
  goal: string;
  title: string;
}
```

### 3.3 update_plan()：更新计划

每执行完一个 Step，PlannerAgent 需要根据执行结果更新剩余步骤：

```python
async def update_plan(self, plan: Plan, step: Step) -> AsyncGenerator[BaseEvent, None]:
    query = UPDATE_PLAN_PROMPT.format(
        plan=plan.model_dump_json(),
        step=step.model_dump_json(),
    )
    
    async for event in self.invoke(query):
        if isinstance(event, MessageEvent):
            parsed_obj = await self._json_parser.invoke(event.message)
            updated_plan = Plan.model_validate(parsed_obj)
            
            # 关键：只更新未完成的步骤，保留已完成的历史
            new_steps = [Step.model_validate(s) for s in updated_plan.steps]
            first_pending_index = next(
                (i for i, s in enumerate(plan.steps) if not s.done), None
            )
            
            if first_pending_index is not None:
                plan.steps = plan.steps[:first_pending_index] + new_steps
            
            yield PlanEvent(plan=plan, status=PlanEventStatus.UPDATED)
        else:
            yield event
```

**为什么只更新未完成的步骤？**

已完成的步骤是"历史事实"，不可更改。Planner 只能重新规划"未来"。这是**事件不可变性**思想的体现。

---

## 4. ReActAgent：执行智能体

ReActAgent 的职责是"动手执行"。它基于 **ReAct（Reasoning + Acting）** 范式：观察 → 思考 → 行动 → 观察...

### 4.1 配置

```python
class ReActAgent(BaseAgent):
    name: str = "react"
    _system_prompt: str = SYSTEM_PROMPT + REACT_SYSTEM_PROMPT
    _format: str = "json_object"
```

- 允许调用工具（`_tool_choice` 未设置，默认 auto）
- 最终输出也是 JSON（包含 success、result、attachments）

### 4.2 execute_step()：执行单个步骤

```python
async def execute_step(self, plan: Plan, step: Step, message: Message) -> AsyncGenerator[BaseEvent, None]:
    query = EXECUTION_PROMPT.format(
        message=message.message,
        attachments="\n".join(message.attachments),
        language=plan.language,
        step=step.description,
    )
    
    # 1. 标记步骤开始
    step.status = ExecutionStatus.RUNNING
    yield StepEvent(step=step, status=StepEventStatus.STARTED)
    
    # 2. 调用 ReAct 循环
    async for event in self.invoke(query):
        if isinstance(event, ToolEvent):
            # 特殊处理 message_ask_user 工具
            if event.function_name == "message_ask_user":
                if event.status == ToolEventStatus.CALLING:
                    yield MessageEvent(role="assistant", message=event.function_args.get("text", ""))
                elif event.status == ToolEventStatus.CALLED:
                    yield WaitEvent()  # 中断执行，等待用户回复
                    return
                continue
        
        elif isinstance(event, MessageEvent):
            # LLM 返回了最终结果（JSON 格式）
            step.status = ExecutionStatus.COMPLETED
            parsed_obj = await self._json_parser.invoke(event.message)
            new_step = Step.model_validate(parsed_obj)
            
            step.success = new_step.success
            step.result = new_step.result
            step.attachments = new_step.attachments
            
            yield StepEvent(step=step, status=StepEventStatus.COMPLETED)
            
            if step.result:
                yield MessageEvent(role="assistant", message=step.result)
            continue
        
        elif isinstance(event, ErrorEvent):
            step.status = ExecutionStatus.FAILED
            step.error = event.error
            yield StepEvent(step=step, status=StepEventStatus.FAILED)
        
        yield event
    
    step.status = ExecutionStatus.COMPLETED
```

**EXECUTION_PROMPT 的核心要求**：

```
你正在执行任务：{step}

注意事项：
- 是你来执行这个任务，而不是用户。不要告诉用户"如何做"，而是直接通过工具"去做"。
- 必须使用 message_notify_user 工具向用户通报进度
- 如果你需要用户提供输入，必须使用 message_ask_user 工具

返回格式：
interface Response {
  success: boolean;
  attachments: string[];  // 生成的文件路径
  result: string;         // 任务结果文本
}
```

### 4.3 summarize()：最终总结

所有步骤执行完毕后，ReActAgent 汇总所有结果，生成最终回复：

```python
async def summarize(self) -> AsyncGenerator[BaseEvent, None]:
    async for event in self.invoke(SUMMARIZE_PROMPT):
        if isinstance(event, MessageEvent):
            parsed_obj = await self._json_parser.invoke(event.message)
            message = Message.model_validate(parsed_obj)
            
            attachments = [File(filepath=fp) for fp in message.attachments]
            yield MessageEvent(role="assistant", message=message.message, attachments=attachments)
        else:
            yield event
```

---

## 5. PlannerReActFlow：流程状态机

Flow 是 Agent 的"总指挥"，管理整个任务执行的状态转移。

### 5.1 状态定义

```python
class FlowStatus(str, Enum):
    IDLE = "idle"              # 空闲
    PLANNING = "planning"      # 生成计划
    EXECUTING = "executing"    # 执行步骤
    UPDATING = "updating"      # 更新计划
    SUMMARIZING = "summarizing" # 总结结果
    COMPLETED = "completed"    # 完成
```

### 5.2 状态转移图

```
                    ┌─────────────┐
                    │    IDLE     │
                    └──────┬──────┘
                           │ 收到用户消息
                           ▼
                    ┌─────────────┐
           ┌───────│  PLANNING   │◄────────┐
           │       └──────┬──────┘         │
           │              │ 生成计划        │
           │              ▼                │
           │       ┌─────────────┐         │
           │       │  EXECUTING  │         │
           │       └──────┬──────┘         │
           │              │ 执行步骤        │
           │              ▼                │
           │       ┌─────────────┐         │
           │       │  UPDATING   │─────────┘ 更新计划后继续执行
           │       └─────────────┘   （循环）
           │
           │       （所有步骤完成）
           │              │
           │              ▼
           │       ┌─────────────┐
           └──────►│ SUMMARIZING │
                   └──────┬──────┘
                          │ 总结完成
                          ▼
                   ┌─────────────┐
                   │  COMPLETED  │
                   └──────┬──────┘
                          │
                          ▼
                        IDLE
```

### 5.3 invoke()：状态机主循环

```python
async def invoke(self, message: Message) -> AsyncGenerator[BaseEvent, None]:
    session = await self._uow.session.get_by_id(self._session_id)
    
    # 处理"打断"场景：用户在中途发送了新消息
    if session.status != SessionStatus.PENDING:
        await self.planner.roll_back(message)
        await self.react.roll_back(message)
    
    if session.status == SessionStatus.RUNNING:
        self.status = FlowStatus.PLANNING  # 新消息需要重新规划
    
    if session.status == SessionStatus.WAITING:
        self.status = FlowStatus.EXECUTING  # 用户回复了，继续执行
    
    # 标记会话为运行中
    await self._uow.session.update_status(self._session_id, SessionStatus.RUNNING)
    
    # 获取当前最新计划
    self.plan = session.get_latest_plan()
    
    # 状态机主循环
    while True:
        if self.status == FlowStatus.IDLE:
            self.status = FlowStatus.PLANNING
            
        elif self.status == FlowStatus.PLANNING:
            # 调用 PlannerAgent 生成计划
            async for event in self.planner.create_plan(message):
                if isinstance(event, PlanEvent) and event.status == PlanEventStatus.CREATED:
                    self.plan = event.plan
                    yield TitleEvent(title=event.plan.title)
                    yield MessageEvent(role="assistant", message=event.plan.message)
                yield event
            
            self.status = FlowStatus.EXECUTING
            
            if not self.plan or len(self.plan.steps) == 0:
                self.status = FlowStatus.COMPLETED  # 无法生成计划
                
        elif self.status == FlowStatus.EXECUTING:
            self.plan.status = ExecutionStatus.RUNNING
            step = self.plan.get_next_step()
            
            if not step:
                self.status = FlowStatus.SUMMARIZING
                continue
            
            # 调用 ReActAgent 执行步骤
            async for event in self.react.execute_step(self.plan, step, message):
                yield event
            
            # 压缩记忆，避免 Token 爆炸
            await self.react.compact_memory()
            self.status = FlowStatus.UPDATING
            
        elif self.status == FlowStatus.UPDATING:
            # 根据执行结果更新计划
            async for event in self.planner.update_plan(self.plan, step):
                yield event
            self.status = FlowStatus.EXECUTING
            
        elif self.status == FlowStatus.SUMMARIZING:
            async for event in self.react.summarize():
                yield event
            self.status = FlowStatus.COMPLETED
            
        elif self.status == FlowStatus.COMPLETED:
            self.plan.status = ExecutionStatus.COMPLETED
            self.status = FlowStatus.IDLE
            yield PlanEvent(status=PlanEventStatus.COMPLETED, plan=self.plan)
            break
    
    yield DoneEvent()
```

### 5.4 状态机的优雅之处

1. **单一职责**：每个状态只做一件事
2. **可中断**：用户随时可以发送新消息，Flow 会根据当前状态决定是重新规划还是继续执行
3. **可观察**：每个状态转移都产生事件，前端可以显示"AI 正在规划..."、"AI 正在执行第 2 步..."
4. **可恢复**：如果服务重启，从数据库中恢复 Session 的状态和事件历史，可以重建 Flow 状态

---

## 6. 提示词工程（Prompt Engineering）

Faber 的提示词设计非常精细，所有提示词位于 `domain/services/prompts/`。

### 6.1 系统提示词（SYSTEM_PROMPT）

所有 Agent 共享的基础提示词，约 100 行，包含：

- **intro**：Agent 的专长和能力范围
- **language_settings**：默认中文，工作语言一致性要求
- **system_capability**：Linux 沙箱、编程、MCP、A2A
- **file_rules**：必须使用文件工具而非 Shell 操作文件
- **search_rules**：优先专用搜索工具，必须访问原始页面
- **browser_rules**：可见元素格式、交互方式
- **shell_rules**：自动确认、管道、避免交互式命令
- **coding_rules**：代码必须先保存到文件
- **writing_rules**：严禁列表格式、必须详尽、必须引用来源
- **sandbox_environment**：Ubuntu 22.04、Python 3.10、Node.js 20
- **important_notes**：必须亲自执行，禁止交付 Todo list

### 6.2 Planner 专属提示词

```
你是一个任务规划智能体，你需要：
1. 分析用户的消息并理解用户的需求
2. 确定完成任务需要使用哪些工具
3. 根据用户的消息确定工作语言
4. 生成计划的目标和步骤

注意：
- 步骤必须是原子性且独立的
- 步骤描述必须足够详细，让执行者能独立完成
```

### 6.3 ReAct 专属提示词

```
你是一个任务执行智能体，你需要按照以下步骤完成任务：
1. 分析事件：理解用户需求和当前状态
2. 选择工具：根据当前状态和任务规划，选择下一个需要调用的工具
3. 等待执行：选定的工具操作将由沙箱环境实际执行
4. 循环迭代：每次迭代原则上只选择一个工具调用
5. 提交结果：将最终结果发送给用户，结果必须详尽且具体
```

### 6.4 提示词设计哲学

1. **结构化输出**：Planner 和 ReAct 都使用 `json_object` 格式，强制 LLM 返回结构化数据
2. **TypeScript 接口**：用 TypeScript 定义期望的 JSON Schema，LLM 对类型系统的理解较好
3. **示例输出**：提供 JSON 示例，降低 LLM 的格式错误率
4. **约束强化**：多次重复关键约束（如"严禁列表格式"、"必须亲自执行"）

---

## 7. JSON Parser（JSON 解析器）

LLM 返回的 JSON 可能有格式问题（多余引号、缺少逗号等）。项目使用 `json-repair` 库：

```python
class RepairJSONParser(JSONParser):
    async def invoke(self, text: str) -> Any:
        try:
            return json_repair.loads(text)
        except Exception:
            return {}
```

这是一个防御性设计：即使 LLM 输出了"几乎正确"的 JSON，也能自动修复。

---

*Agent 架构是 Faber 的核心竞争力。Planner + ReAct 的分工让复杂任务的处理变得可控：Planner 负责"战略"，ReAct 负责"战术"，Flow 负责"调度"。下一章将深入讲解工具系统——Agent 的"手脚"是如何设计和实现的。*
