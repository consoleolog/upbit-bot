import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path

logger = logging.getLogger(__name__)

STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM)

# 종료할 때 실행 중인 작업을 기다리는 최대 시간(초). 근거 있는 값이 아니며 운영하며 조정한다
DEFAULT_SHUTDOWN_TIMEOUT = 10.0
# 취소한 작업이 끝나기를 기다리는 최대 시간(초). 취소는 다음 await에서 바로 반영되므로 짧게 둔다
DEFAULT_CANCEL_TIMEOUT = 1.0

Job = Callable[[], Awaitable[None]]
Service = Callable[[], Awaitable[None]]


class Application:
    """봇의 최상단 컴포넌트. 마켓별 작업을 받아 처리하고, 종료 요청을 받으면 멈춘다.

    - 다른 마켓의 작업은 동시에, 같은 마켓의 작업은 받은 순서대로 하나씩 실행한다.
    - 한 작업이 실패해도 다른 작업과 애플리케이션은 계속 실행한다.
    """

    def __init__(
        self,
        config_path: Path,
        shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT,
        cancel_timeout: float = DEFAULT_CANCEL_TIMEOUT,
    ) -> None:
        self._config_path = config_path
        self._shutdown_timeout = shutdown_timeout
        self._cancel_timeout = cancel_timeout
        self._stop_requested = asyncio.Event()
        self._accepting = False
        self._market_locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._services: list[tuple[str, Service]] = []
        self._service_tasks: list[asyncio.Task[None]] = []

    def add_service(self, name: str, service: Service) -> None:
        """실행하는 동안 함께 돌릴 백그라운드 작업(예: 스케줄러)을 등록한다. run() 전에만 등록할 수 있다."""
        if self._accepting or self._stop_requested.is_set():
            raise RuntimeError("services must be added before run()")
        self._services.append((name, service))

    async def run(self) -> None:
        """종료 요청(stop 또는 SIGINT·SIGTERM)이 올 때까지 작업을 받아 처리한다."""
        loop = asyncio.get_running_loop()
        for sig in STOP_SIGNALS:
            loop.add_signal_handler(sig, self.stop)
        self._accepting = True
        logger.info("application started (config=%s)", self._config_path)
        try:
            for name, service in self._services:
                task = asyncio.create_task(service(), name=f"service:{name}")
                task.add_done_callback(self._on_service_done)
                self._service_tasks.append(task)
            await self._stop_requested.wait()
        finally:
            self._accepting = False
            # 새 작업을 만드는 서비스를 먼저 멈춘 뒤, 이미 받은 작업을 정리한다
            await self._stop_services()
            await self._drain_tasks()
            # 실행이 끝난 뒤에도 신호 처리기가 남아 있으면, 이후의 Ctrl+C가 이미 끝난 애플리케이션으로 전달된다
            for sig in STOP_SIGNALS:
                loop.remove_signal_handler(sig)
            logger.info("application stopped")

    def stop(self) -> None:
        """종료를 요청한다. 여러 번 호출해도 한 번만 멈춘다."""
        if not self._stop_requested.is_set():
            logger.info("stop requested")
            self._stop_requested.set()

    def submit(self, market: str, job: Job) -> bool:
        """마켓의 작업을 받는다. 실행 중이 아니거나 종료가 요청됐으면 받지 않고 False를 돌려준다."""
        # stop() 직후 run()이 깨어나 접수를 막기 전까지의 틈에 들어오는 작업도 막는다
        if not self._accepting or self._stop_requested.is_set():
            logger.warning("job rejected: application is not running (market=%s)", market)
            return False
        task = asyncio.create_task(self._run_job(market, job), name=f"job:{market}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def _run_job(self, market: str, job: Job) -> None:
        lock = self._market_locks.setdefault(market, asyncio.Lock())
        async with lock:
            # 잠금을 기다리는 사이 종료가 요청됐으면 새로 시작하지 않는다. 종료 중에 새 주문이 나가지 않게 하기 위해서다
            if self._stop_requested.is_set():
                logger.warning("job skipped: stop requested before it started (market=%s)", market)
                return
            try:
                await job()
            except Exception:
                # 한 마켓의 실패가 다른 마켓과 애플리케이션을 멈추지 않도록 여기서 끊는다.
                # 취소(CancelledError)는 Exception이 아니므로 잡지 않고 그대로 전달된다
                logger.exception("job failed (market=%s)", market)

    def _on_service_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        # 서비스(스케줄러 등)가 멈추면 봇이 아무 일도 하지 않은 채 살아 있게 되므로, 애플리케이션을 종료한다
        error = task.exception()
        if error is not None:
            logger.error("service %s failed; stopping application", task.get_name(), exc_info=error)
        else:
            logger.error("service %s exited unexpectedly; stopping application", task.get_name())
        self.stop()

    async def _stop_services(self) -> None:
        for task in self._service_tasks:
            task.cancel()
        if self._service_tasks:
            _, stuck = await asyncio.wait(self._service_tasks, timeout=self._cancel_timeout)
            if stuck:
                logger.error("%d service(s) did not stop after cancel; shutting down without them", len(stuck))

    async def _drain_tasks(self) -> None:
        """실행 중인 작업이 끝나기를 기다리고, 시간 안에 끝나지 않은 작업은 취소한다."""
        if not self._tasks:
            return
        _, pending = await asyncio.wait(self._tasks, timeout=self._shutdown_timeout)
        if pending:
            logger.warning("cancelling %d job(s) not finished within %.1fs", len(pending), self._shutdown_timeout)
            for task in pending:
                task.cancel()
            # 작업이 취소를 무시하면 끝없이 기다리게 되므로 여기에도 시간 제한을 둔다
            _, stuck = await asyncio.wait(pending, timeout=self._cancel_timeout)
            if stuck:
                logger.error("%d job(s) did not stop after cancel; shutting down without them", len(stuck))
