import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from src.app.application import Application
from src.app.config import ConfigError, TradingConfig, load_config
from src.app.scheduler import CandleScheduler
from src.connections.database import Database, DatabaseConfig
from src.exchange.ticker_feed import TickerFeed
from src.exchange.upbit_client import UpbitClient
from src.repositories.market import MarketRepository
from src.trading.market_worker import MarketWorker
from src.trading.realtime_exit import RealtimeExit

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_STARTUP_FAILED = 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="upbit-bot", description="업비트 자동매매 봇")
    parser.add_argument("--config", type=Path, required=True, help="설정 파일 경로")
    return parser.parse_args(argv)


async def run(config_path: Path, config: TradingConfig, db_config: DatabaseConfig) -> None:
    database = Database(db_config)
    await database.connect()
    # 연결은 애플리케이션이 작업을 모두 정리한 뒤에 닫는다 (업비트 → DB 순서)
    try:
        market_ids = await register_markets(database, config.markets)
        async with UpbitClient() as upbit:
            app = Application(config_path)
            worker = MarketWorker(
                upbit=upbit,
                database=database,
                timeframe=config.timeframe,
                indicator_settings=config.indicator,
                signal_settings=config.signal,
                exit_settings=config.exit,
                stochastic_entry_max=config.stochastic_entry_max,
                market_ids=market_ids,
            )
            scheduler = CandleScheduler(
                timeframe=config.timeframe,
                markets=config.markets,
                submit=app.submit,
                job_factory=worker.candle_close_job,
            )
            app.add_service(f"scheduler:{config.timeframe}", scheduler.run)
            # 캔들 마감 사이에는 현재가로 보유 중인 모의 포지션의 손절·익절만 판단한다 (매매 로직 5.1)
            realtime_exit = RealtimeExit(database=database, market_ids=market_ids, submit=app.submit)
            feed = TickerFeed(markets=config.markets, on_ticker=realtime_exit.on_ticker)
            app.add_service("ticker", feed.run)
            await app.run()
    finally:
        await database.close()


async def register_markets(database: Database, markets: Sequence[str]) -> dict[str, int]:
    """설정의 마켓을 등록하고(이미 있으면 그대로) 마켓 코드별 market_id를 돌려준다."""
    repo = MarketRepository()
    async with database.transaction() as conn:
        for market in markets:
            await repo.add(conn, market)
        return {market: (await repo.get_by_symbol(conn, market)).market_id for market in markets}


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)

    # 잘못된 설정은 매매를 시작하기 전에 막는다
    try:
        config = load_config(args.config)
    except ConfigError as error:
        logger.error("invalid config: %s", error)
        return EXIT_STARTUP_FAILED
    try:
        db_config = DatabaseConfig.from_env("DB_")
    except ValueError as error:
        logger.error("invalid database config: %s", error)
        return EXIT_STARTUP_FAILED

    asyncio.run(run(args.config, config, db_config))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
