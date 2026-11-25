import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from src.app.application import Application
from src.app.config import ConfigError, TradingConfig, load_config
from src.app.notifier import DiscordNotifier, Notifier, NullNotifier
from src.app.scheduler import CandleScheduler
from src.connections.database import Database, DatabaseConfig
from src.exchange.account import MARKET_STATE_ACTIVE, OrderChance
from src.exchange.ticker_feed import TickerFeed
from src.exchange.upbit_auth import UpbitCredentials
from src.exchange.upbit_client import UpbitClient, UpbitError
from src.repositories.market import MarketRepository
from src.repositories.models import MarketHalt
from src.trading.live import LiveExecutor
from src.trading.live_trader import LiveTrader
from src.trading.market_worker import MarketWorker
from src.trading.realtime_exit import RealtimeExit
from src.trading.status_report import StatusReporter

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
    config_path: Path,
    config: TradingConfig,
    db_config: DatabaseConfig,
    credentials: UpbitCredentials | None = None,
    notifier: Notifier | None = None,
) -> None:
    """봇을 실행한다. 실거래(config.live)면 credentials가 있어야 하고, 시작 전에 업비트 계좌를 확인한다.

    notifier가 없으면 알림을 보내지 않는다.
    """
    notifier = notifier or NullNotifier()
    if config.live and credentials is None:
        raise StartupError("live trading needs upbit credentials")
    database = Database(db_config)
    await database.connect()
    # 연결은 애플리케이션이 작업을 모두 정리한 뒤에 닫는다 (업비트 → DB 순서)
    try:
        market_ids = await register_markets(database, config.markets)
        async with UpbitClient(credentials=credentials) as upbit:
            live = None
            executor: LiveExecutor | None = None
            halts: dict[str, MarketHalt] = {}
            if config.live:
                chances = await check_live_account(upbit, config.markets)
                executor = LiveExecutor(
                    upbit=upbit,
                    database=database,
                    exit_settings=config.exit,
                    ask_min_totals={market: chance.ask_min_total for market, chance in chances.items()},
                    notifier=notifier,
                )
                # 이전 실행에서 중단된 마켓은 사람이 해제할 때까지 계속 막는다
                halts = await executor.load_halts(market_ids)
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
                stage_filter=config.stage_filter,
            )
            scheduler = CandleScheduler(
                timeframe=config.timeframe,
                markets=config.markets,
                submit=app.submit,
                job_factory=worker.candle_close_job,
            )
            app.add_service(f"scheduler:{config.timeframe}", scheduler.run)
            # 캔들 마감 사이에는 현재가로 보유 중인 포지션의 손절·익절만 판단한다 (매매 로직 5.1)
            realtime_exit = RealtimeExit(
                database=database, market_ids=market_ids, submit=app.submit, live=live, order_settings=config.order
            )
            feed = TickerFeed(markets=config.markets, on_ticker=realtime_exit.on_ticker)
            app.add_service("ticker", feed.run)
            if isinstance(notifier, DiscordNotifier):
                app.add_service("notifier", notifier.run)
            if executor is not None:
                # 중단이 이어지는 동안 몇 시간마다 다시 알린다
                app.add_service("halt-reminder", executor.remind_halts)
            # 정해진 시각마다 상태를 알린다. 정상이어도 보내므로 오지 않으면 봇에 문제가 있다는 신호다
            reporter = StatusReporter(
                database=database,
                market_ids=market_ids,
                prices=realtime_exit.latest_ticker,
                notifier=notifier,
                submit=app.submit,
                stochastic_entry_max=config.stochastic_entry_max,
                started_at=datetime.now(UTC),
                balance=upbit if config.live else None,
                halts=executor,
                stage_filter=config.stage_filter,
                stages=worker.latest_stage,
                clock=lambda: datetime.now(UTC),
            )
            app.add_service("status-report", reporter.run)
            started = f"[봇 시작] {'실거래' if config.live else '모의 실행'} · {', '.join(config.markets)} · {config.timeframe}"
            halted = "".join(f"\n🚨 매매 중단 중: {market} ({halt.reason})" for market, halt in halts.items())
            notifier.notify(started + halted)
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


def configure_logging() -> None:
    """로그 설정. httpx·httpcore는 요청마다 전체 URL을 INFO로 남기므로 경고 이상만 남긴다.

    디스코드 웹훅 URL에는 토큰이 들어 있어 URL 자체가 비밀값이다 (로그에 남기지 않음).
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
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

    notifier: Notifier = NullNotifier()
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if webhook_url:
        try:
            notifier = DiscordNotifier(webhook_url)
        except ValueError as error:
            logger.error("invalid notification config: %s", error)
            return EXIT_STARTUP_FAILED

    credentials = None
    if config.live:
        try:
            credentials = UpbitCredentials.from_env()
        except ValueError as error:
            logger.error("invalid upbit credentials: %s", error)
            return EXIT_STARTUP_FAILED

    try:
        asyncio.run(run(args.config, config, db_config, credentials, notifier))
    except StartupError as error:
        logger.error("startup check failed: %s", error)
        # 애플리케이션이 시작되지 않아 알림 서비스가 없으므로 바로 보낸다
        if isinstance(notifier, DiscordNotifier):
            asyncio.run(notifier.send_now(f"🚨 [시작 실패] 매매를 시작하지 않았습니다.\n{error}"))
        return EXIT_STARTUP_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
