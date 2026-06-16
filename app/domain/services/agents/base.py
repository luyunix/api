import asyncio
import logging
import uuid
from abc import ABC
from typing import Optional, List, AsyncGenerator, Dict, Any, Callable

from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.external.memory_batch_writer import MemoryBatchWriter
from app.domain.models.app_config import AgentConfig
from app.domain.models.event import ToolEvent, ToolEventStatus, ErrorEvent, MessageEvent, BaseEvent
from app.domain.models.memory import Memory
from app.domain.models.message import Message
from app.domain.models.tool_result import ToolResult
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.memory.memory_budget import MemoryBudgetManager
from app.domain.services.memory.memory_retriever import MemoryRetriever
from app.domain.services.memory.memory_summarizer import MemorySummarizer
from app.domain.services.memory.token_counter import TokenCounter
from app.domain.services.tools.base import BaseTool
from app.domain.services.prompts.react import REFLECTION_PROMPT

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """基础Agent智能体"""
    name: str = ""  # 智能体名字
    _system_prompt: str = ""  # 系统预设prompt
    _format: Optional[str] = None  # Agent的响应格式
    _retry_interval: float = 1.0  # 重试间隔
    _tool_choice: Optional[str] = None  # 强制选择工具

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            session_id: str,  # 会话id
            agent_config: AgentConfig,  # Agent配置
            llm: LLM,  # 语言模型协议
            json_parser: JSONParser,  # JSON输出解析器
            tools: List[BaseTool],  # 工具列表
            memory_batch_writer: Optional[MemoryBatchWriter] = None,  # 记忆批量写入器
            budget_manager: Optional[MemoryBudgetManager] = None,  # Token预算管理器
            summarizer: Optional[MemorySummarizer] = None,  # 记忆摘要器
            memory_retriever: Optional[MemoryRetriever] = None,  # 记忆检索器
    ) -> None:
        """构造函数，完成Agent的初始化"""
        self._uow_factory = uow_factory
        self._uow = uow_factory()
        self._session_id = session_id
        self._agent_config = agent_config
        self._llm = llm
        self._memory: Optional[Memory] = None
        self._json_parser = json_parser
        self._tools = tools
        self._memory_batch_writer = memory_batch_writer
        self._budget_manager = budget_manager
        self._summarizer = summarizer
        self._memory_retriever = memory_retriever or MemoryBudgetManager(budget=llm.max_tokens)

    async def _ensure_memory(self) -> None:
        """确保智能体记忆是存在的"""
        if self._memory is None:
            async with self._uow:
                self._memory = await self._uow.session.get_memory(self._session_id, self.name)
            # 设置预算管理器
            if self._budget_manager and self._memory:
                self._memory.set_budget_manager(self._budget_manager)

    def _get_available_tools(self) -> List[Dict[str, Any]]:
        """获取Agent所有可用的工具列表参数声明/Schema"""
        available_tools = []
        for tool in self._tools:
            available_tools.extend(tool.get_tools())
        return available_tools

    def _get_tool(self, tool_name: str) -> BaseTool:
        """获取对应工具所在的工具集/包"""
        # 1.循环遍历所有工具包
        for tool in self._tools:
            # 2.判断工具包中是否存在该工具
            if tool.has_tool(tool_name):
                return tool

        raise ValueError(f"未知工具: {tool_name}")

    async def _invoke_llm(self, messages: List[Dict[str, Any]], format: Optional[str] = None) -> Dict[str, Any]:
        """调用语言模型并处理记忆内容"""
        # 1.将消息添加到记忆中
        await self._add_to_memory(messages)

        # 2.检索相关历史经验并注入 episodic_notes
        if self._memory_retriever and self._memory:
            # 从用户消息中提取查询文本
            query = " ".join(
                msg.get("content", "") for msg in messages if msg.get("role") == "user"
            )
            if query and len(query.strip()) > 5:
                try:
                    results = await self._memory_retriever.retrieve_for_planner(query) \
                        if self.name == "planner" else \
                        await self._memory_retriever.retrieve_for_react(query)

                    if results:
                        logger.info(f"Agent[{self.name}] 检索到 {len(results)} 条相关历史经验")
                        for result in results:
                            note = self._memory_retriever.format_as_episodic_note(result)
                            self._memory.add_episodic_note(note)
                        # 持久化更新后的记忆
                        await self._persist_memory()
                except Exception as e:
                    logger.warning(f"Agent[{self.name}] 检索历史经验失败: {e}")

        # 3.检查 Token 预算,接近上限时触发智能压缩
        if self._budget_manager and self._memory:
            compacted = self._budget_manager.check_and_compact(self._memory)
            if compacted:
                # 压缩后重新持久化
                await self._persist_memory()
                budget_report = self._budget_manager.get_budget_report()
                logger.info(f"Agent[{self.name}] Token 预算报告: {budget_report}")

        # 4.组装语言模型的响应格式
        response_format = {"type": format} if format else None

        # 5.循环向LLM发起提问直到最大重试次数
        error = "调用语言模型发生错误"
        for _ in range(self._agent_config.max_retries):
            try:
                # 4.调用语言模型获取响应内容
                message = await self._llm.invoke(
                    messages=self._memory.get_messages(),
                    tools=self._get_available_tools(),
                    response_format=response_format,
                    tool_choice=self._tool_choice,
                )

                # 5.处理AI响应内容避免空回复
                if message.get("role") == "assistant":
                    if not message.get("content") and not message.get("tool_calls"):
                        logger.warning(f"LLM回复了空内容，执行重试")
                        await self._add_to_memory([
                            {"role": "assistant", "content": ""},
                            {"role": "user", "content": "AI无响应内容，请继续。"}
                        ])
                        await asyncio.sleep(self._retry_interval)
                        continue

                    # 6.取出非空消息并处理工具调用(兼容DeepSeek思考模型的写法)
                    filtered_message = {"role": "assistant", "content": message.get("content")}
                    if message.get("reasoning_content"):
                        filtered_message["reasoning_content"] = message.get("reasoning_content")
                    if message.get("tool_calls"):
                        # 7.取出工具调用的数据，限制LLM一次只能调用工具
                        filtered_message["tool_calls"] = message.get("tool_calls")[:1]
                else:
                    # 8.非AI消息则记录日志并存储message
                    logger.warning(f"LLM响应内容无法确认消息角色: {message.get('role')}")
                    filtered_message = message

                # 9.将消息添加到记忆中
                await self._add_to_memory([filtered_message])
                return filtered_message
            except Exception as e:
                # 10.记录日志并睡眠指定的时间
                logger.error(f"调用语言模型发生错误: {str(e)}")
                error = str(e)
                await asyncio.sleep(self._retry_interval)
                continue

        # 11.所有重试均已耗尽仍未获得有效响应，抛出异常避免返回None
        raise RuntimeError(f"调用语言模型失败, 已达到最大重试次数({self._agent_config.max_retries}): {error}")

    async def _invoke_tool(self, tool: BaseTool, tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
        """传递工具包+工具名字+对应参数调用指定工具"""
        # 1.执行循环调用工具获取结果
        err = ""
        for _ in range(self._agent_config.max_retries):
            try:
                return await tool.invoke(tool_name, **arguments)
            except Exception as e:
                err = str(e)
                logger.exception(f"调用工具[{tool_name}]出错, 错误: {str(e)}")
                await asyncio.sleep(self._retry_interval)
                continue

        # 2.循环最大重试次数后没有结果则将错误作为工具的执行结果，让LLM自行处理
        return ToolResult(success=False, message=err)

    async def _persist_memory(self) -> None:
        """将当前记忆持久化到存储中

        如果配置了 MemoryBatchWriter,则使用批量写入;
        否则使用同步直接写入(兼容旧模式)。
        """
        if self._memory_batch_writer:
            await self._memory_batch_writer.enqueue(self._session_id, self.name, self._memory)
        else:
            async with self._uow:
                await self._uow.session.save_memory(self._session_id, self.name, self._memory)

    async def _add_to_memory(self, messages: List[Dict[str, Any]]) -> None:
        """将对应的信息添加到记忆中"""
        # 1.先检查确保记忆是存在的
        await self._ensure_memory()

        # 2.确保 system prompt 始终存在于 system_messages 中
        has_system_prompt = any(
            msg.get("content") == self._system_prompt
            for msg in self._memory.system_messages
        )
        if not has_system_prompt:
            self._memory.system_messages.insert(0, {
                "role": "system", "content": self._system_prompt,
            })

        # 3.将正常消息添加到记忆中（按角色自动分层）
        self._memory.add_messages(messages)

        # 4.将记忆持久化到数据仓库中(批量或同步)
        await self._persist_memory()

    async def compact_memory(self) -> None:
        """压缩Agent的记忆

        1. 执行同步压缩（删除浏览器结果、截断长文本）
        2. 如果配置了 MemorySummarizer，异步为压缩后的消息生成 LLM 摘要
        3. 持久化到存储
        """
        await self._ensure_memory()
        self._memory.compact()

        # 异步生成 LLM 摘要（替代粗暴的 "(removed)"）
        if self._summarizer:
            logger.info(f"Agent[{self.name}] 开始为压缩后的消息生成 LLM 摘要")
            await self._summarizer.batch_summarize(self._memory.working_messages)

        await self._persist_memory()

    async def roll_back(self, message: Message) -> None:
        """Agent的状态回滚，该函数用于确保Agent的消息列表状态是正确，用于发送新消息、暂停/停止任务、通知用户"""
        # 1.取出记忆中的最后一条消息，检查是否是工具调用
        await self._ensure_memory()
        last_message = self._memory.get_last_message()
        if (
                not last_message or
                not last_message.get("tool_calls") or
                len(last_message.get("tool_calls")) == 0
        ):
            return

        # 2.取出消息中的工具调用参数
        tool_call = last_message.get("tool_calls")[0]

        # 3.提取工具名字、id
        function_name = tool_call.get("function", {}).get("name")
        tool_call_id = tool_call.get("id")

        # 4.判断下当前的工具是不是通知用户(message_ask_user)
        if function_name == "message_ask_user":
            self._memory.add_message({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "function_name": function_name,
                "content": message.model_dump_json(),
            })
        else:
            # 5.否则直接删除最后一条消息
            self._memory.roll_back()

        # 6.将记忆持久化
        await self._persist_memory()

    async def invoke(self, query: str, format: Optional[str] = None, plan_context: Optional[Dict[str, Any]] = None) -> AsyncGenerator[BaseEvent, None]:
        """传递消息+响应格式+可选的计划上下文调用程序生成异步迭代内容"""
        # 1.需要判断下是否传递了format
        format = format if format else self._format

        # 2.调用语言模型获取响应内容
        message = await self._invoke_llm(
            [{"role": "user", "content": query}],
            format,
        )

        # 3.循环遍历直到最大迭代次数
        # 如果传入了计划上下文（ReAct执行步骤），则使用单步迭代预算；否则使用全局预算
        iteration_limit = (
            min(self._agent_config.max_iterations_per_step, self._agent_config.max_iterations)
            if plan_context
            else self._agent_config.max_iterations
        )
        for loop_index in range(1, iteration_limit + 1):
            # 4.如果LLM响应为空或无工具调用则表示LLM生成了文本回答，这时候就是最终答案
            if not message or not message.get("tool_calls"):
                break

            # 5.循环遍历工具参数并执行
            tool_messages = []
            for tool_call in message["tool_calls"]:
                if not tool_call.get("function"):
                    continue

                # 6.取出调用工具id、名字、参数信息
                tool_call_id = tool_call["id"] or str(uuid.uuid4())
                function_name = tool_call["function"]["name"]
                function_args = await self._json_parser.invoke(tool_call["function"]["arguments"])

                # 7.取出Agent中对应的工具
                tool = self._get_tool(function_name)

                # 8.返回工具即将调用事件，其中tool_content比较特殊，需要在具体业务中进行实现，这里留空即可
                yield ToolEvent(
                    tool_call_id=tool_call_id,
                    tool_name=tool.name,
                    function_name=function_name,
                    function_args=function_args,
                    status=ToolEventStatus.CALLING,
                )

                # 9.调用工具并获取结果
                result = await self._invoke_tool(tool, function_name, function_args)

                # 10.返回工具调用结果，其中tool_content比较特殊，需要在业务中进行实现
                yield ToolEvent(
                    tool_call_id=tool_call_id,
                    tool_name=tool.name,
                    function_name=function_name,
                    function_args=function_args,
                    function_result=result,
                    status=ToolEventStatus.CALLED,
                )

                # 11.组装工具响应
                tool_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "function_name": function_name,
                    "content": result.model_dump_json(),
                })

            # 12.如果配置了计划上下文且到达反思间隔，则插入反思提示
            if (
                plan_context
                and self._agent_config.reflection_interval > 0
                and loop_index % self._agent_config.reflection_interval == 0
            ):
                reflection_message = self._build_reflection_message(plan_context)
                tool_messages.insert(0, {
                    "role": "user",
                    "content": reflection_message,
                })
                logger.info(f"Agent[{self.name}] 触发第 {loop_index} 轮反思检查点")

            # 13.所有工具都执行完成后，调用LLM获取汇总消息二次提供
            message = await self._invoke_llm(tool_messages)
        else:
            # 14.超过最大迭代次数后，则抛出错误
            yield ErrorEvent(error=f"Agent迭代超过最大迭代次数: {iteration_limit}, 任务处理失败")

        # 15.在指定步骤内完成了迭代则返回消息事件
        if message and message.get("content") is not None:
            yield MessageEvent(message=message["content"])
        else:
            yield ErrorEvent(error="Agent未能生成有效回复内容")

    def _build_reflection_message(self, plan_context: Dict[str, Any]) -> str:
        """根据计划上下文构建反思提示消息"""
        return REFLECTION_PROMPT.format(
            goal=plan_context.get("goal", ""),
            title=plan_context.get("title", ""),
            current_step_index=plan_context.get("current_step_index", 0),
            total_steps=plan_context.get("total_steps", 0),
            current_step_description=plan_context.get("current_step", ""),
            completed_steps=plan_context.get("completed_steps", "无"),
            remaining_steps=plan_context.get("remaining_steps", "无"),
        )
