import asyncio
import logging
import signal
from pathlib import Path

logger = logging.getLogger(__name__)

STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM)


class Application:
    """봇의 최상단 컴포넌트. 시작하고, 종료 신호를 받을 때까지 실행한 뒤 멈춘다."""

    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path
        self._stop_requested = asyncio.Event()

    async def run(self) -> None:
        """종료 요청(stop 또는 SIGINT·SIGTERM)이 올 때까지 실행한다."""
        loop = asyncio.get_running_loop()
        for sig in STOP_SIGNALS:
            loop.add_signal_handler(sig, self.stop)
        logger.info("application started (config=%s)", self._config_path)
        try:
            await self._stop_requested.wait()
        finally:
            # 실행이 끝난 뒤에도 신호 처리기가 남아 있으면, 이후의 Ctrl+C가 이미 끝난 애플리케이션으로 전달된다
            for sig in STOP_SIGNALS:
                loop.remove_signal_handler(sig)
            logger.info("application stopped")

    def stop(self) -> None:
        """종료를 요청한다. 여러 번 호출해도 한 번만 멈춘다."""
        if not self._stop_requested.is_set():
            logger.info("stop requested")
            self._stop_requested.set()
