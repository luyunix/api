import logging
import time
from typing import AsyncGenerator, Optional, Callable

from app.domain.external.browser import Browser
from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.external.memory_batch_writer import MemoryBatchWriter
from app.domain.external.sandbox import Sandbox
from app.domain.external.search import SearchEngine
from app.domain.services.memory.episodic_memory_service import EpisodicMemoryService
from app.domain.services.memory.memory_budget import MemoryCompactor
from app.domain.models.app_config import AgentConfig
from app.domain.models.event import BaseEvent, PlanEvent, PlanEventStatus, TitleEvent, MessageEvent
from app.domain.models.event import DoneEvent
from app.domain.models.message import Message
from app.domain.models.plan import Plan, Step, ExecutionStatus
from app.domain.models.session import SessionStatus
from app.domain.services.agents.planner import PlannerAgent
from app.domain.services.agents.react import ReActAgent
from app.domain.services.tools.a2a import A2ATool
from app.domain.services.tools.browser import BrowserTool
from app.domain.services.tools.file import FileTool
from app.domain.services.tools.mcp import MCPTool
from app.domain.services.tools.message import MessageTool
from app.domain.services.tools.search import SearchTool
from app.domain.services.tools.shell import ShellTool
from .base import BaseFlow, FlowStatus
from ...repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)


class PlannerReActFlow(BaseFlow):
    """规划与执行流"""

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],  # uow模块
            llm: LLM,  # 大语言模型
            agent_config: AgentConfig,  # 智能体配置
            session_id: str,  # 会话id
            json_parser: JSONParser,  # JSON解析器
            browser: Browser,  # 浏览器
            sandbox: Sandbox,  # 沙箱
            search_engine: SearchEngine,  # 搜索引擎
            mcp_tool: MCPTool,  # mcp工具
            a2a_tool: A2ATool,  # a2a远程agent
            memory_batch_writer: MemoryBatchWriter | None = None,  # 记忆批量写入器
            memory_compactor: MemoryCompactor | None = None,  # 记忆压缩器（token 预算）
            episodic_memory_service: EpisodicMemoryService | None = None,  # 情景记忆服务
    ) -> None:
        """构造函数，完成规划与执行流的初始化"""
        # 1.流初始化数据配置
        self._uow_factory = uow_factory
        self._uow = uow_factory()
        self._session_id = session_id
        self.status = FlowStatus.IDLE
        self.plan: Optional[Plan] = None
        self._start_time: Optional[float] = None

        # 2.初始化Agent预设工具列表
        tools = [
            FileTool(sandbox=sandbox),
            ShellTool(sandbox=sandbox),
            BrowserTool(browser=browser),
            SearchTool(search_engine=search_engine),
            MessageTool(),
            mcp_tool,
            a2a_tool,
        ]

        # 3.创建规划Agent
        self.planner = PlannerAgent(
            uow_factory=uow_factory,
            session_id=session_id,
            agent_config=agent_config,
            llm=llm,
            json_parser=json_parser,
            tools=tools,
            memory_batch_writer=memory_batch_writer,
            memory_compactor=memory_compactor,
            episodic_memory_service=episodic_memory_service,
        )
        logger.debug(f"创建规划Agent成功, 会话id: {self._session_id}")

        # 4.创建执行Agent
        self.react = ReActAgent(
            uow_factory=uow_factory,
            session_id=session_id,
            agent_config=agent_config,
            llm=llm,
            json_parser=json_parser,
            tools=tools,
            memory_batch_writer=memory_batch_writer,
            memory_compactor=memory_compactor,
            episodic_memory_service=episodic_memory_service,
        )
        logger.debug(f"创建执行Agent成功, 会话id: {self._session_id}")

        # 5.记忆依赖（情景记忆写入用）
        self._episodic_memory_service = episodic_memory_service

    async def invoke(self, message: Message) -> AsyncGenerator[BaseEvent, None]:
        """传递消息，运行流，在六中调用planner&react智能体组合完成任务并返回对应事件"""
        # 1.调用会话仓库查询会话是否存在
        async with self._uow:
            session = await self._uow.session.get_by_id(self._session_id)
        if not session:
            raise ValueError(f"会话[{self._session_id}]不存在, 请核实后尝试")

        # 2.判断会话的状态是不是空闲
        #   如果不是则有可能有两种状态
        #    - 任务未结束，还在运行，但是用户又传递一条消息
        #    - Agent在等待人类输入，这时候人类输入了
        #   这时候均需要处理历史消息列表，避免AI(工具调用消息)后直接接上人类消息
        if session.status != SessionStatus.PENDING:
            logger.debug(f"会话[{self._session_id}]未处于空闲状态，回滚数据确保消息列表格式正确")
            await self.planner.roll_back(message)
            await self.react.roll_back(message)

        # 3.如果会话状态等于运行中，则流需要重新规划内容/plan
        if session.status == SessionStatus.RUNNING:
            logger.debug(f"会话[{self._session_id}]处于运行状态并传递了新消息")
            self.status = FlowStatus.PLANNING

        # 4.如果会话状态等于等待人类输入，则需要修改流的状态为执行中
        if session.status == SessionStatus.WAITING:
            logger.debug(f"会话[{self._session_id}]处于等待状态并传递了新消息")
            self.status = FlowStatus.EXECUTING

        # 5.更新会话状态为运行中
        async with self._uow:
            await self._uow.session.update_status(self._session_id, SessionStatus.RUNNING)

        # 5.1 记录任务开始时间，用于任务级超时检查
        self._start_time = time.time()

        # 6.获取当前会话中最新事件
        self.plan = session.get_latest_plan()
        logger.info(f"Planner&ReAct流接收消息: {message.message[:50]}...")

        # 7.定义当前正在执行的子步骤
        step = None

        # 8.创建死循环执行任务，根据流的不同状态执行不同的操作
        while True:
            # 8.1 检查任务是否超时
            if self._start_time is not None:
                elapsed = time.time() - self._start_time
                if elapsed > self._agent_config.task_timeout_seconds:
                    logger.error(
                        f"Planner&ReAct流任务超时，已运行 {elapsed:.1f}s，"
                        f"超过 {self._agent_config.task_timeout_seconds}s，强制终止"
                    )
                    self.plan.status = ExecutionStatus.FAILED
                    self.plan.error = f"任务执行超过 {self._agent_config.task_timeout_seconds} 秒，已终止"
                    self.status = FlowStatus.COMPLETED
                    yield ErrorEvent(
                        error=f"任务执行超过 {self._agent_config.task_timeout_seconds} 秒，已终止"
                    )
                    yield PlanEvent(status=PlanEventStatus.COMPLETED, plan=self.plan)
                    break

            # 9.如果流的状态为空闲，则只需要将状态修改为规划中
            if self.status == FlowStatus.IDLE:
                logger.info(f"Planner&ReAct流状态从{FlowStatus.IDLE}变成{FlowStatus.PLANNING}")
                self.status = FlowStatus.PLANNING
            elif self.status == FlowStatus.PLANNING:
                # 10.流状态为规划中，则调用规划Agent
                logger.info(f"Planner&ReAct流开始创建计划/Plan")
                async for event in self.planner.create_plan(message):
                    # 11.判断规划Agent是否返回规划事件
                    if isinstance(event, PlanEvent) and event.status == PlanEventStatus.CREATED:
                        # 12.创建计划成功时需要更新计划
                        self.plan = event.plan
                        logger.info(f"Planner&ReAct流成功创建计划, 共计: {len(event.plan.steps)} 步")

                        # 13.在计划中同步生成了会话标题+初始AI消息
                        yield TitleEvent(title=event.plan.title)
                        yield MessageEvent(role="assistant", message=event.plan.message)

                    # 14.将生成的事件直接输出(一般来说是PlanEvent)
                    yield event

                # 15.计划创建完成，更新流状态为执行中
                logger.info(f"Planner&ReAct流状态从{FlowStatus.PLANNING}变成{FlowStatus.EXECUTING}")
                self.status = FlowStatus.EXECUTING

                # 16.判断计划是否生成，步骤是否正常
                if not self.plan or len(self.plan.steps) == 0:
                    logger.info(f"Planner&ReAct流创建计划失败或无子步骤")
                    self.status = FlowStatus.COMPLETED
            elif self.status == FlowStatus.EXECUTING:
                # 17.流的状态为执行中，先将计划状态调整为运行中，同时调用执行Agent完成每个子步骤
                self.plan.status = ExecutionStatus.RUNNING

                # 18.获取当前计划的下一个需要执行的子步骤
                step = self.plan.get_next_step()

                # 19.如果不存在下一个需要执行的自己花，则更新流状态并执行后续步骤
                if not step:
                    logger.info(f"Planner&ReAct流状态从{FlowStatus.EXECUTING}变成{FlowStatus.SUMMARIZING}")
                    self.status = FlowStatus.SUMMARIZING
                    continue

                # 20.调用执行Agent执行对应的步骤
                logger.info(f"Planner&ReAct流开始执行步骤 {step.id}: {step.description[:50]}...")
                async for event in self.react.execute_step(self.plan, step, message):
                    yield event

                # 20.1 提前完成检测：如果当前步骤结果包含提前完成标记，且全局目标已满足，则跳过剩余步骤
                if (
                    self._agent_config.enable_early_completion
                    and step.result
                    and self.react.detect_early_completion(step.result)
                ):
                    remaining_steps = [s for s in self.plan.steps if not s.done and s.id != step.id]
                    if remaining_steps:
                        logger.info(f"步骤 {step.id} 触发提前完成标记，检查全局目标是否已满足")
                        goal_satisfied = await self._check_goal_satisfied(self.plan, step)
                        if goal_satisfied:
                            logger.info("全局目标已提前达成，跳过剩余步骤并进入总结")
                            self.status = FlowStatus.SUMMARIZING
                            continue

                # 21.压缩执行Agent记忆，避免上下文腐化+消耗大量token
                logger.info(f"压缩{self.react.name} Agent记忆/上下文")
                await self.react.compact_memory()

                # 22.将状态更新为updating
                self.status = FlowStatus.UPDATING
            elif self.status == FlowStatus.UPDATING:
                # 23.流状态为更新表示需要更新计划
                logger.info(f"Planner&ReAct流开始更新计划")
                async for event in self.planner.update_plan(self.plan, step):
                    yield event

                # 24.计划更新完成，需要执行相应的子步骤
                logger.info(f"Planner&ReAct流状态从{FlowStatus.UPDATING}变成{FlowStatus.EXECUTING}")
                self.status = FlowStatus.EXECUTING
            elif self.status == FlowStatus.SUMMARIZING:
                # 25.流状态为总结中，则意味着所有子步骤都执行完成
                logger.info(f"Planner&ReAct流开始总结")
                async for event in self.react.summarize():
                    yield event

                # 26.总结完毕，意味着流即将结束
                logger.info(f"Planner&ReAct流状态从{FlowStatus.SUMMARIZING}变成{FlowStatus.COMPLETED}")
                self.status = FlowStatus.COMPLETED
            elif self.status == FlowStatus.COMPLETED:
                # 27.计划状态已完成则更新plan状态，并发送计划事件通知API已完成
                self.plan.status = ExecutionStatus.COMPLETED

                # 28.情景记忆学习：任务完成后提炼可复用经验写入 pgvector（学习闭环）
                #    仅在情景记忆启用时执行，失败不影响主流程
                if self._episodic_memory_service:
                    try:
                        await self._episodic_memory_service.index_task(
                            self._session_id, self.react.name, self.plan, message
                        )
                    except Exception as e:
                        logger.warning(f"情景记忆写入失败: {e}")

                self.status = FlowStatus.IDLE
                yield PlanEvent(status=PlanEventStatus.COMPLETED, plan=self.plan)
                break
        # 28.任务已经结束则返回结束事件
        yield DoneEvent()
        logger.info(f"Planner&ReAct流处理任务消息已完毕")

    @property
    def done(self) -> bool:
        """只读属性，返回流是否运行结束"""
        return self.status == FlowStatus.IDLE

    async def _check_goal_satisfied(self, plan: Plan, completed_step: Step) -> bool:
        """轻量级检查：判断全局目标是否已被已完成的步骤满足"""
        remaining = [
            s.description for s in plan.steps
            if not s.done and s.id != completed_step.id
        ]
        prompt = f"""
你正在判断一个多步骤任务的全局目标是否已经提前达成。

任务目标: {plan.goal}
当前已完成步骤: {completed_step.description}
该步骤结果摘要: {completed_step.result or "无"}
剩余待执行步骤: {remaining or "无"}

请仅回答 yes 或 no：当前已完成的步骤是否已经满足了全局任务目标？
如果剩余步骤仍然必要，请回答 no。
"""
        try:
            response = await self.planner._llm.invoke(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
            )
            content = response.get("content", "").lower()
            satisfied = "yes" in content or "是" in content
            logger.info(f"提前完成检测: 全局目标是否已达成={satisfied}, LLM回复={content[:50]}")
            return satisfied
        except Exception as e:
            logger.warning(f"提前完成检测调用LLM失败: {str(e)}")
            return False
