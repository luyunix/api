import logging
from typing import Any, AsyncGenerator, Optional

from app.domain.models.event import (
    StepEventStatus,
    StepEvent,
    ToolEvent,
    MessageEvent,
    ErrorEvent,
    ToolEventStatus,
    WaitEvent,
    BaseEvent
)
from app.domain.models.file import File
from app.domain.models.message import Message
from app.domain.models.plan import Plan, Step, ExecutionStatus
from app.domain.services.prompts.react import REACT_SYSTEM_PROMPT, EXECUTION_PROMPT, SUMMARIZE_PROMPT, REFLECTION_PROMPT
from app.domain.services.prompts.system import SYSTEM_PROMPT
from .base import BaseAgent

logger = logging.getLogger(__name__)


class ReActAgent(BaseAgent):
    """基于ReAct架构的执行Agent"""
    name: str = "react"
    _system_prompt: str = SYSTEM_PROMPT + REACT_SYSTEM_PROMPT
    _format: str = "json_object"  # format控制的是content、工具调用控制的是tool_calls两者不冲突

    @staticmethod
    def detect_early_completion(text: Optional[str]) -> bool:
        """检测文本中是否包含提前完成标记"""
        if not text:
            return False
        markers = [
            "[EARLY_COMPLETE]",
            "已提前完成",
            "任务已提前达成",
            "early complete",
            "无需进一步操作",
            "目标已达成",
        ]
        text_lower = text.lower()
        return any(marker.lower() in text_lower for marker in markers)

    @classmethod
    def _extract_step_payload(cls, parsed_obj: Any) -> dict[str, Any]:
        """从模型输出中提取符合步骤结果结构的对象。

        json_repair 在遇到 `[EARLY_COMPLETE] {...}` 这类混合输出时可能返回列表，
        这里递归查找真正包含 success/result/attachments 的 dict，避免 Pydantic
        直接校验列表时报错。
        """
        if isinstance(parsed_obj, dict):
            if any(key in parsed_obj for key in ("success", "result", "attachments")):
                return parsed_obj
            for value in parsed_obj.values():
                try:
                    return cls._extract_step_payload(value)
                except ValueError:
                    continue

        if isinstance(parsed_obj, list):
            for item in parsed_obj:
                try:
                    return cls._extract_step_payload(item)
                except ValueError:
                    continue

        raise ValueError(f"未找到有效的步骤执行结果: {parsed_obj}")

    async def execute_step(self, plan: Plan, step: Step, message: Message) -> AsyncGenerator[BaseEvent, None]:
        """根据传递的消息+规划+子步骤，执行相应的子步骤"""
        # 1.构建全局计划上下文
        current_step_index = next(
            (i for i, s in enumerate(plan.steps) if s.id == step.id),
            -1,
        ) + 1
        completed_steps = [
            f"- [完成] {s.description}"
            for s in plan.steps
            if s.done and s.id != step.id
        ]
        remaining_steps = [
            f"- [待执行] {s.description}"
            for s in plan.steps
            if not s.done and s.id != step.id
        ]
        plan_context = {
            "goal": plan.goal,
            "title": plan.title,
            "current_step": step.description,
            "completed_steps": "\n".join(completed_steps) if completed_steps else "无",
            "remaining_steps": "\n".join(remaining_steps) if remaining_steps else "无",
            "current_step_index": current_step_index,
            "total_steps": len(plan.steps),
        }

        # 2.根据传递的内容生成执行消息
        query = EXECUTION_PROMPT.format(
            message=message.message,
            attachments="\n".join(message.attachments),
            language=plan.language,
            step=step.description,
            goal=plan_context["goal"],
            title=plan_context["title"],
            current_step=plan_context["current_step"],
            completed_steps=plan_context["completed_steps"],
            remaining_steps=plan_context["remaining_steps"],
            current_step_index=plan_context["current_step_index"],
            total_steps=plan_context["total_steps"],
            success_criteria=step.success_criteria or "无明确验收标准，请尽力完成该步骤描述的任务",
        )

        # 3.更新步骤的执行状态为运行中并返回Step事件
        step.status = ExecutionStatus.RUNNING
        yield StepEvent(step=step, status=StepEventStatus.STARTED)

        # 4.调用invoke获取agent返回的事件内容
        async for event in self.invoke(query, plan_context=plan_context):
            # 4.判断事件类型执行不同操作
            if isinstance(event, ToolEvent):
                # 5.工具事件需要判断工具的名称是否为message_ask_user
                if event.function_name == "message_ask_user":
                    # 6.工具如果在调用中，我们需要返回一条消息告知用户需要让用户处理什么
                    if event.status == ToolEventStatus.CALLING:
                        yield MessageEvent(
                            role="assistant",
                            message=event.function_args.get("text", "")
                        )
                    elif event.status == ToolEventStatus.CALLED:
                        # 7.如果工具事件为已调用，则需要返回等待事件并中断程序
                        yield WaitEvent()
                        return
                    continue
            elif isinstance(event, MessageEvent):
                # 8.返回消息事件，意味着content有内容，content有内容则代表执行Agent已运行完毕
                step.status = ExecutionStatus.COMPLETED

                # 9.message中输出的数据结构为json，需要提取并解析
                parsed_obj = await self._json_parser.invoke(event.message)
                step_payload = self._extract_step_payload(parsed_obj)
                step_payload.setdefault("id", step.id)
                step_payload.setdefault("description", step.description)
                new_step = Step.model_validate(step_payload)

                # 10.更新子步骤的数据
                step.success = new_step.success
                step.result = new_step.result
                step.attachments = new_step.attachments

                # 11.返回步骤完成事件
                yield StepEvent(step=step, status=StepEventStatus.COMPLETED)

                # 12.如果子步骤拿到了结果，还需要返回一段消息给用户(将结果返回给用户)
                if step.result:
                    yield MessageEvent(role="assistant", message=step.result)
                continue
            elif isinstance(event, ErrorEvent):
                # 13.错误事件更新步骤的状态
                step.status = ExecutionStatus.FAILED
                step.error = event.error

                # 14.返回子步骤对应事件
                yield StepEvent(step=step, status=StepEventStatus.FAILED)

            # 15.其他场景将事件直接返回
            yield event

        # 16.循环迭代完成后代表子步骤已实现，需要更新状态
        step.status = ExecutionStatus.COMPLETED

    async def summarize(self) -> AsyncGenerator[BaseEvent, None]:
        """调用Agent汇总历史的消息并生成最终回复+附件"""
        # 1.构建请求query
        query = SUMMARIZE_PROMPT

        # 2.调用invoke方法获取Agent生成的事件
        async for event in self.invoke(query):
            # 3.判断事件类型是否为消息事件，如果是则表示Agent结构化生成汇总内容
            if isinstance(event, MessageEvent):
                # 4.记录日志并解析输出内容
                logger.info(f"执行Agent生成汇总内容: {event.message}")
                parsed_obj = await self._json_parser.invoke(event.message)

                # 5.将解析数据转换为Message对象
                message = Message.model_validate(parsed_obj)

                # 6.提取消息中的附件信息
                attachments = [File(filepath=filepath) for filepath in message.attachments]

                # 7.返回消息事件并将消息+附件进行相应
                yield MessageEvent(
                    role="assistant",
                    message=message.message,
                    attachments=attachments,
                )
            else:
                # 8.其他事件则直接返回
                yield event
