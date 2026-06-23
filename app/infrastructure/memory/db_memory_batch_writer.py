import asyncio
import logging
from typing import Callable, Dict, Tuple

from app.domain.external.memory_batch_writer import MemoryBatchWriter
from app.domain.models.memory import Memory
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)


class DBMemoryBatchWriter(MemoryBatchWriter):
    """基于数据库的记忆批量写入器

    使用 asyncio.Queue 收集写入请求,后台任务按批次或定时 flush 到 PostgreSQL。
    相同 (session_id, agent_name) 的写入请求会自动去重,只保留最新的一份。
    """

    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        batch_size: int = 10,
        flush_interval: float = 3.0,
    ) -> None:
        """构造函数

        :param uow_factory: UoW 工厂函数
        :param batch_size: 每批最多写入多少条记忆
        :param flush_interval: 定时刷新间隔(秒)
        """
        self._uow_factory = uow_factory
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._queue: asyncio.Queue[Tuple[str, str, Memory]] = asyncio.Queue()
        self._flush_task: asyncio.Task | None = None
        self._running = False

    async def enqueue(self, session_id: str, agent_name: str, memory: Memory) -> None:
        """将记忆写入请求放入队列"""
        await self._queue.put((session_id, agent_name, memory))
        logger.debug(f"MemoryBatchWriter 入队: session={session_id}, agent={agent_name}, queue_size={self._queue.qsize()}")

    async def start(self) -> None:
        """启动后台刷新任务"""
        if self._running:
            logger.warning("MemoryBatchWriter 已启动,无需重复操作")
            return
        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info(f"MemoryBatchWriter 已启动, batch_size={self._batch_size}, interval={self._flush_interval}s")

    async def shutdown(self) -> None:
        """优雅关闭:停止循环,flush 剩余队列"""
        if not self._running:
            return
        self._running = False
        logger.info("MemoryBatchWriter 正在关闭...")

        if self._flush_task:
            try:
                await asyncio.wait_for(self._flush_task, timeout=self._flush_interval + 2)
            except asyncio.TimeoutError:
                logger.warning("MemoryBatchWriter 后台任务关闭超时,强制取消")
                self._flush_task.cancel()

        # flush 剩余队列
        await self._drain_queue()
        logger.info("MemoryBatchWriter 关闭成功")

    async def flush(self) -> None:
        """立即刷新队列中的所有记忆"""
        await self._drain_queue()

    async def _flush_loop(self) -> None:
        """后台循环:定时或定批量刷新"""
        while self._running:
            try:
                # 等待队列有新数据或超时
                await asyncio.wait_for(
                    self._queue.get(),
                    timeout=self._flush_interval,
                )
                self._queue.task_done()

                # 如果队列积累到一定量,立即刷新
                if self._queue.qsize() >= self._batch_size - 1:
                    await self._drain_queue()

            except asyncio.TimeoutError:
                # 超时但队列有数据,刷新
                if not self._queue.empty():
                    await self._drain_queue()

    async def _drain_queue(self) -> None:
        """将队列中的记忆批量写入数据库(自动去重)"""
        if self._queue.empty():
            return

        # 1. 从队列中取出数据,用 Dict 去重(相同 key 只保留最新的)
        batch: Dict[Tuple[str, str], Memory] = {}
        while not self._queue.empty() and len(batch) < self._batch_size:
            try:
                session_id, agent_name, memory = self._queue.get_nowait()
                batch[(session_id, agent_name)] = memory
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

        if not batch:
            return

        # 2. 批量写入数据库（带重试，失败时 ERROR 级别告警，不再静默吞错）
        logger.info(f"MemoryBatchWriter 批量写入 {len(batch)} 条记忆")
        max_retries = 2
        last_error: Exception | None = None
        for attempt in range(1, max_retries + 2):  # 初试 + 最多 max_retries 次重试
            try:
                uow = self._uow_factory()
                async with uow:
                    for (session_id, agent_name), memory in batch.items():
                        await uow.session.save_memory(session_id, agent_name, memory)
                # 写入成功
                if attempt > 1:
                    logger.info(f"MemoryBatchWriter 第 {attempt} 次尝试写入成功")
                return
            except Exception as e:
                last_error = e
                if attempt <= max_retries:
                    logger.warning(
                        f"MemoryBatchWriter 批量写入失败(第 {attempt} 次)，将重试: {e}"
                    )
                    await asyncio.sleep(0.5 * attempt)  # 指数退避
                else:
                    # 重试耗尽：ERROR 级别告警，含受影响的会话/Agent，便于排查
                    affected = ", ".join(f"{s}/{a}" for (s, a) in batch.keys())
                    logger.error(
                        f"MemoryBatchWriter 批量写入最终失败(已重试 {max_retries} 次)，"
                        f"受影响记忆[{affected}]: {e}"
                    )
                    # 不抛异常打断 Agent，但记忆可能丢失——已通过 ERROR 日志暴露
