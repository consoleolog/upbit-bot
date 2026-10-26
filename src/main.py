import argparse
import asyncio
import signal
import sys
from pathlib import Path

import structlog
import yaml

# 프로젝트 루트를 sys.path에 추가 — src.X 형태의 절대 import 활성화
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.monitoring import StructuredLogger
from src.orchestrator import Orchestrator

logger = structlog.get_logger(__name__)


def _load_config(path: str) -> dict:
    """YAML 설정 파일을 읽어 딕셔너리로 반환한다."""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"설정 파일을 찾을 수 없습니다: {path}")
    with config_path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


async def run_orchestrator(args: argparse.Namespace) -> None:
    config = _load_config(args.config)

    StructuredLogger("Trading")

    logger.info("main.starting", config_path=args.config, mode=config.get("mode"))

    orchestrator = Orchestrator(
        mode=config["mode"], markets=config["markets"], candle_types=config["candle_types"], config=config
    )

    # Unix 계열에서만 loop-level 시그널 핸들러 등록
    if sys.platform != "win32":
        loop = asyncio.get_running_loop()

        _shutdown_tasks: set[asyncio.Task] = set()

        def _handle_shutdown(sig_num: int) -> None:
            logger.info("main.shutdown_requested", signal=sig_num)
            task = asyncio.create_task(orchestrator.shutdown())
            _shutdown_tasks.add(task)
            task.add_done_callback(_shutdown_tasks.discard)

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _handle_shutdown, sig)

    try:
        await orchestrator.run()
    except KeyboardInterrupt:
        logger.info("main.keyboard_interrupt")
        await orchestrator.shutdown()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Strategy Trading Bot")
    parser.add_argument(
        "-c", "--config", type=str, required=True, help="설정 파일 경로 (예: config/trading/config.yaml)"
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run_orchestrator(parse_args()))
