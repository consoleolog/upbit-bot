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
from src.exchange.account import MARKET_STATE_ACTIVE, OrderChance
from src.exchange.ticker_feed import TickerFeed
from src.exchange.upbit_auth import UpbitCredentials
from src.exchange.upbit_client import UpbitClient, UpbitError
from src.repositories.market import MarketRepository
from src.trading.live import LiveExecutor
from src.trading.live_trader import LiveTrader
from src.trading.market_worker import MarketWorker
from src.trading.realtime_exit import RealtimeExit

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_STARTUP_FAILED = 1


class StartupError(Exception):
    """매매를 시작하기 전 확인에 실패함. 봇을 시작하지 않는다."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="upbit-bot", description="업비트 자동매매 봇")
    parser.add_argument("--config", type=Path, required=True, help="설정 파일 경로")
    return parser.parse_args(argv)


async def run(
    config_path: Path, config: TradingConfig, db_config: DatabaseConfig, credentials: UpbitCredentials | None = None
) -> None:
    """봇을 실행한다. 실거래(config.live)면 credentials가 있어야 하고, 시작 전에 업비트 계좌를 확인한다."""
    if config.live and credentials is None:
        raise StartupError("live trading needs upbit credentials")
    database = Database(db_config)
    await database.connect()
    # 연결은 애플리케이션이 작업을 모두 정리한 뒤에 닫는다 (업비트 → DB 순서)
    try:
        market_ids = await register_markets(database, config.markets)
        async with UpbitClient(credentials=credentials) as upbit:
            live = None
            if config.live:
                chances = await check_live_account(upbit, config.markets)
                executor = LiveExecutor(
                    upbit=upbit,
                    database=database,
                    exit_settings=config.exit,
                    ask_min_totals={market: chance.ask_min_total for market, chance in chances.items()},
                )
                live = LiveTrader(
                    executor=executor,
                    upbit=upbit,
                    database=database,
                    chances=chances,
                    order_settings=config.order,
                    exit_settings=config.exit,
                    stochastic_entry_max=config.stochastic_entry_max,
                )
            logger.info("trading mode: %s", "live" if config.live else "paper")
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
                live=live,
                order_settings=config.order,
            )
            scheduler = CandleScheduler(
                timeframe=config.timeframe,
                markets=config.markets,
                submit=app.submit,
                job_factory=worker.candle_close_job,
            )
            app.add_service(f"scheduler:{config.timeframe}", scheduler.run)
            # 캔들 마감 사이에는 현재가로 보유 중인 포지션의 손절·익절만 판단한다 (매매 로직 5.1)
            realtime_exit = RealtimeExit(database=database, market_ids=market_ids, submit=app.submit, live=live)
            feed = TickerFeed(markets=config.markets, on_ticker=realtime_exit.on_ticker)
            app.add_service("ticker", feed.run)
            await app.run()
    finally:
        await database.close()


async def check_live_account(upbit: UpbitClient, markets: Sequence[str]) -> dict[str, OrderChance]:
    """실거래를 시작하기 전에 키(권한·허용 IP)와 마켓별 주문 가능 여부를 확인한다. 실패하면 StartupError.

    마켓별 주문 가능 정보(수수료율, 최소·최대 주문 금액)를 돌려준다.
    """
    chances = {}
    try:
        await upbit.get_accounts()
        for market in markets:
            chance = await upbit.get_order_chance(market)
            if chance.state != MARKET_STATE_ACTIVE:
                raise StartupError(f"market is not tradable: {market} (state={chance.state})")
            chances[market] = chance
    except UpbitError as error:
        # 오류 메시지에는 키가 들어가지 않는다 (UpbitClient가 쿼리·헤더를 넣지 않음)
        raise StartupError(f"upbit account check failed: {error}") from error
    return chances


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

    credentials = None
    if config.live:
        try:
            credentials = UpbitCredentials.from_env()
        except ValueError as error:
            logger.error("invalid upbit credentials: %s", error)
            return EXIT_STARTUP_FAILED

    try:
        asyncio.run(run(args.config, config, db_config, credentials))
    except StartupError as error:
        logger.error("startup check failed: %s", error)
        return EXIT_STARTUP_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
