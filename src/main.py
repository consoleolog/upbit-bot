import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from src.application import Application
from src.config import ConfigError, TradingConfig, load_config
from src.indicators import IndicatorSettings
from src.market_worker import MarketWorker
from src.scheduler import CandleScheduler
from src.upbit_client import UpbitClient

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_STARTUP_FAILED = 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="upbit-bot", description="업비트 자동매매 봇")
    parser.add_argument("--config", type=Path, required=True, help="설정 파일 경로")
    return parser.parse_args(argv)


async def run(config_path: Path, config: TradingConfig) -> None:
    # 연결은 애플리케이션이 작업을 모두 정리한 뒤에 닫는다
    async with UpbitClient() as upbit:
        app = Application(config_path)
        # 지표 기간은 설정 파일에서 읽기 전까지 매매 로직 2.1의 기본값을 쓴다
        worker = MarketWorker(upbit=upbit, timeframe=config.timeframe, settings=IndicatorSettings())
        scheduler = CandleScheduler(
            timeframe=config.timeframe, markets=config.markets, submit=app.submit, job_factory=worker.candle_close_job
        )
        app.add_service(f"scheduler:{config.timeframe}", scheduler.run)
        await app.run()


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)

    # 잘못된 설정은 매매를 시작하기 전에 막는다
    try:
        config = load_config(args.config)
    except ConfigError as error:
        logger.error("invalid config: %s", error)
        return EXIT_STARTUP_FAILED

    asyncio.run(run(args.config, config))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
