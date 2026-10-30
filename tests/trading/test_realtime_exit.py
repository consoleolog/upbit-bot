import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from src.app.application import Job
from src.app.config import ExitSettings
from src.connections.database import Database
from src.exchange.ticker import Ticker
from src.repositories.market import MarketRepository
from src.repositories.models import PositionStatus
from src.repositories.paper_position import PaperPositionRepository
from src.trading.realtime_exit import RealtimeExit
from src.trading.reasons import Reason

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _ticker(price: str, market: str = "KRW-BTC", seconds: int = 1) -> Ticker:
    return Ticker(market=market, price=Decimal(price), traded_at=T0 + timedelta(minutes=30, seconds=seconds))


class Submitted:
    """애플리케이션 대신 넘겨받은 작업을 모아 두고, 원할 때 실행한다."""

    def __init__(self, accept: bool = True) -> None:
        self.accept = accept
        self.jobs: list[tuple[str, Job]] = []

    def __call__(self, market: str, job: Job) -> bool:
        if self.accept:
            self.jobs.append((market, job))
        return self.accept

    async def run_next(self) -> None:
        _, job = self.jobs.pop(0)
        await job()


def _exit(submitted: Submitted) -> RealtimeExit:
    return RealtimeExit(database=MagicMock(), market_ids={"KRW-BTC": 1, "KRW-ETH": 2}, submit=submitted)


async def test_many_tickers_make_one_job_using_latest_price(mocker: MockerFixture) -> None:
    submitted = Submitted()
    realtime = _exit(submitted)
    check = mocker.patch.object(realtime, "check", new_callable=mocker.AsyncMock)

    for price in ("100", "99", "98"):
        realtime.on_ticker(_ticker(price))

    # 기다리는 작업이 있으면 새로 넘기지 않는다
    assert [market for market, _ in submitted.jobs] == ["KRW-BTC"]
    await submitted.run_next()
    # 실행할 때 가장 최근 현재가로 판단한다
    check.assert_awaited_once_with("KRW-BTC", _ticker("98"))


async def test_ticker_during_check_makes_next_job(mocker: MockerFixture) -> None:
    submitted = Submitted()
    realtime = _exit(submitted)

    async def check(market: str, ticker: Ticker) -> None:
        # 판단하는 동안 새 현재가가 온다
        realtime.on_ticker(_ticker("97", seconds=2))

    mocker.patch.object(realtime, "check", side_effect=check)
    realtime.on_ticker(_ticker("100"))
    await submitted.run_next()

    assert len(submitted.jobs) == 1


async def test_markets_are_independent(mocker: MockerFixture) -> None:
    submitted = Submitted()
    realtime = _exit(submitted)
    mocker.patch.object(realtime, "check", new_callable=mocker.AsyncMock)

    realtime.on_ticker(_ticker("100", "KRW-BTC"))
    realtime.on_ticker(_ticker("3000000", "KRW-ETH"))

    assert [market for market, _ in submitted.jobs] == ["KRW-BTC", "KRW-ETH"]


async def test_rejected_job_does_not_block_next_ticker(mocker: MockerFixture) -> None:
    submitted = Submitted(accept=False)
    realtime = _exit(submitted)
    mocker.patch.object(realtime, "check", new_callable=mocker.AsyncMock)

    realtime.on_ticker(_ticker("100"))
    submitted.accept = True
    realtime.on_ticker(_ticker("99"))

    assert len(submitted.jobs) == 1


async def test_failed_check_does_not_block_next_ticker(mocker: MockerFixture) -> None:
    submitted = Submitted()
    realtime = _exit(submitted)
    mocker.patch.object(realtime, "check", side_effect=RuntimeError("db down"))
    realtime.on_ticker(_ticker("100"))

    # 실패는 애플리케이션이 기록한다. 대기 표시는 남지 않아 다음 현재가로 다시 판단한다
    with pytest.raises(RuntimeError, match="db down"):
        await submitted.run_next()
    realtime.on_ticker(_ticker("99"))

    assert len(submitted.jobs) == 1


# ---------------------------------------------------------------------------
# 실제 PostgreSQL (TEST_DB_* 가 없으면 건너뜀)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_check_sells_on_stop_loss_and_logs(database: Database, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    symbol = "KRW-T" + uuid.uuid4().hex[:8].upper()
    async with database.transaction() as conn:
        market = await MarketRepository().add(conn, symbol)
        assert market is not None
        await PaperPositionRepository().open(
            conn,
            market_id=market.market_id,
            entry_time=T0,
            entry_price=Decimal("100"),
            average_price=Decimal("100.05"),
            exit_settings=ExitSettings(),
        )
    realtime = RealtimeExit(database=database, market_ids={symbol: market.market_id}, submit=Submitted())
    try:
        await realtime.check(symbol, _ticker("96", symbol))

        async with database.transaction() as conn:
            row = await conn.fetchrow("SELECT * FROM paper_position WHERE market_id = $1", market.market_id)
        assert (row["status"], row["exit_reason"], row["exit_price"]) == (
            PositionStatus.CLOSED,
            Reason.STOP_LOSS,
            Decimal("96"),
        )
        assert f"paper SELL realtime (market={symbol}, reason=STOP_LOSS" in caplog.text
    finally:
        async with database.transaction() as conn:
            await conn.execute("DELETE FROM paper_position WHERE market_id = $1", market.market_id)
            await conn.execute("DELETE FROM market WHERE market_id = $1", market.market_id)
