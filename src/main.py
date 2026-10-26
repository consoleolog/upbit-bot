import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from src.application import Application, Job
from src.config import ConfigError, load_config
from src.scheduler import CandleScheduler

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_STARTUP_FAILED = 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="upbit-bot", description="업비트 자동매매 봇")
    parser.add_argument("--config", type=Path, required=True, help="설정 파일 경로")
    return parser.parse_args(argv)


def candle_close_job(market: str, close: datetime) -> Job:
    """캔들 마감 작업. 마켓 처리를 구현하기 전까지는 기록만 남긴다."""

    async def job() -> None:
        logger.info("candle close job (market=%s, close=%s)", market, close.isoformat())

    return job


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)

    # 잘못된 설정은 매매를 시작하기 전에 막는다
    try:
        config = load_config(args.config)
    except ConfigError as error:
        logger.error("invalid config: %s", error)
        return EXIT_STARTUP_FAILED

    app = Application(args.config)
    scheduler = CandleScheduler(
        timeframe=config.timeframe, markets=config.markets, submit=app.submit, job_factory=candle_close_job
    )
    app.add_service(f"scheduler:{config.timeframe}", scheduler.run)
    asyncio.run(app.run())
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
