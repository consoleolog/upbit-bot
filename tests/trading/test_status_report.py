import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio

from src.app.config import ExitSettings
from src.connections.database import Database
from src.exchange.account import Account
from src.exchange.ticker import Ticker
from src.exchange.upbit_client import UpbitAuthError, UpbitConnectionError
from src.repositories.indicator_info import IndicatorInfoRepository
from src.repositories.market import MarketRepository
from src.repositories.models import IndicatorInfo, IndicatorType, LiveOrderState
from src.repositories.order import OrderRepository
from src.repositories.paper_position import PaperPositionRepository
from src.repositories.position import PositionRepository
from src.trading.reasons import Reason
from src.trading.signals import CrossState, OverState
from src.trading.status_report import JOB_NAME, StatusReporter, next_report_time

# 한국 시간 11-23 09:00 = UTC 00:00
AT = datetime(2026, 11, 23, 0, 0, tzinfo=UTC)
SINCE = AT - timedelta(hours=4)
EXIT = ExitSettings(take_profit_rate=Decimal("1.5"), stop_loss_rate=Decimal("3"), min_profit_rate=Decimal("0.5"))


@pytest.mark.parametrize(
    ("now_kst", "expected_kst"),
    [
        ("2026-11-23 08:59:59", "2026-11-23 09:00"),
        ("2026-11-23 09:00:00", "2026-11-23 13:00"),  # 같은 시각은 제외 (방금 보냄)
        ("2026-11-23 09:00:01", "2026-11-23 13:00"),
        ("2026-11-23 21:30:00", "2026-11-24 01:00"),  # 날짜가 넘어감
        ("2026-11-23 00:10:00", "2026-11-23 01:00"),
    ],
)
def test_next_report_time(now_kst: str, expected_kst: str) -> None:
    kst = timedelta(hours=9)
    now = datetime.fromisoformat(now_kst).replace(tzinfo=UTC) - kst

    assert next_report_time(now).astimezone(UTC) + kst == datetime.fromisoformat(expected_kst).replace(tzinfo=UTC)


class Notifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def notify(self, text: str) -> None:
        self.messages.append(text)


class Balance:
    def __init__(self, result: list[Account] | Exception) -> None:
        self.result = result
        self.calls = 0

    async def get_accounts(self) -> list[Account]:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class Halts:
    def __init__(self, markets: list[str]) -> None:
        self.markets = markets

    def halted_markets(self) -> list[str]:
        return self.markets


def _krw(balance: str) -> Account:
    return Account(
        currency="KRW", balance=Decimal(balance), locked=Decimal(0), avg_buy_price=Decimal(0), unit_currency="KRW"
    )


async def _no_sleep(seconds: float) -> None:
    pass


def _reporter(
    database: Database,
    market_ids: dict[str, int],
    *,
    prices: dict[str, Decimal] | None = None,
    balance: Balance | None = None,
    halts: Halts | None = None,
    notifier: Notifier | None = None,
) -> StatusReporter:
    def price(market: str) -> Ticker | None:
        value = (prices or {}).get(market)
        return None if value is None else Ticker(market=market, price=value, traded_at=AT)

    return StatusReporter(
        database=database,
        market_ids=market_ids,
        prices=price,
        notifier=notifier or Notifier(),
        submit=lambda name, job: True,
        stochastic_entry_max=50.0,
        started_at=SINCE,
        balance=balance,
        halts=halts,
        clock=lambda: AT,
        sleep=_no_sleep,
    )


@pytest_asyncio.fixture
async def markets(database: Database) -> AsyncIterator[dict[str, int]]:
    """커밋된 마켓 둘 (보유용, 매도·신호용). 상태 알림은 자기 연결로 읽으므로 시험이 끝나면 직접 지운다."""
    ids: dict[str, int] = {}
    async with database.transaction() as conn:
        for _ in range(2):
            market = await MarketRepository().add(conn, "KRW-T" + uuid.uuid4().hex[:8].upper())
            assert market is not None
            ids[market.symbol] = market.market_id
    try:
        yield ids
    finally:
        async with database.transaction() as conn:
            market_ids = list(ids.values())
            await conn.execute("DELETE FROM orders WHERE market_id = ANY($1::int[])", market_ids)
            await conn.execute("DELETE FROM position WHERE market_id = ANY($1::int[])", market_ids)
            await conn.execute("DELETE FROM paper_position WHERE market_id = ANY($1::int[])", market_ids)
            await conn.execute("DELETE FROM indicator_info WHERE market_id = ANY($1::int[])", market_ids)
            await conn.execute("DELETE FROM market WHERE market_id = ANY($1::int[])", market_ids)


@pytest.mark.integration
async def test_live_report(database: Database, markets: dict[str, int]) -> None:
    (held, held_id), (other, other_id) = markets.items()
    positions, orders = PositionRepository(), OrderRepository()
    async with database.transaction() as conn:
        # 보유: 0.002개를 수수료 포함 200,100원에 샀다 (평단 100,050,000)
        await positions.open(
            conn,
            market_id=held_id,
            volume=Decimal("0.002"),
            cost=Decimal("200100"),
            opened_at=SINCE,
            exit_settings=EXIT,
            first_buy_amount=Decimal("200000"),
        )
        # 직전 알림 이후 닫힌 포지션: 10,005원에 사서 10,155원을 받았다 (+150원)
        closed = await positions.open(
            conn,
            market_id=other_id,
            volume=Decimal("0.0001"),
            cost=Decimal("10005"),
            opened_at=SINCE,
            exit_settings=EXIT,
            first_buy_amount=Decimal("10000"),
        )
        assert closed is not None
        await positions.add_sell(
            conn, position_id=closed.position_id, volume=Decimal("0.0001"), proceeds=Decimal("10155")
        )
        await positions.close(
            conn, position_id=closed.position_id, closed_at=SINCE + timedelta(hours=1), exit_reason=Reason.TAKE_PROFIT
        )
        order = await orders.create_buy(conn, market_id=other_id, amount=Decimal("10000"))
        assert order is not None
        await orders.record_result(
            conn,
            order_id=order.order_id,
            state=LiveOrderState.DONE,
            executed_volume=Decimal("0.0001"),
            executed_funds=Decimal("10000"),
            paid_fee=Decimal("5"),
        )
        # 매수 신호: RSI 과매도 + 골든크로스 (2개) + %K·%D 기준 이하 (2개) = 4/9
        for info in (
            IndicatorInfo(
                market_id=other_id,
                indicator_type=IndicatorType.RSI,
                value=30.0,
                signal=29.0,
                candle_time=SINCE,
                over_state=OverState.OVER_SOLD,
                over_time=SINCE,
                cross_state=CrossState.GOLDEN_CROSS,
                cross_time=SINCE,
            ),
            IndicatorInfo(
                market_id=other_id, indicator_type=IndicatorType.STOCHASTIC, value=40.0, signal=45.0, candle_time=SINCE
            ),
        ):
            await IndicatorInfoRepository().save(conn, info)
    # 주문 기록 시각(now())이 SINCE보다 뒤여야 센다
    reporter = _reporter(
        database, markets, prices={held: Decimal("101300000")}, balance=Balance([_krw("380000.7")]), halts=Halts([])
    )

    text = await reporter.build(AT, SINCE, "05:00")

    assert text.split("\n") == [
        "📊 [상태] 11-23 09:00 · 실거래",
        "원화 380,001원 · 매매 중단 없음",
        "보유",
        f"  {held} 0.002개 · 평단 100,050,000 · 현재 +1.25% (수수료 전)",
        "05:00 이후",
        "  체결 1건 · 닫힌 포지션 1건 · 손익 +150원",
        "매수 신호 (9개 조건 중)",
        f"  {held} 0/9 · {other} 4/9",
    ]


@pytest.mark.integration
async def test_paper_report(database: Database, markets: dict[str, int]) -> None:
    (held, held_id), (other, other_id) = markets.items()
    repo = PaperPositionRepository()
    async with database.transaction() as conn:
        await repo.open(
            conn,
            market_id=held_id,
            entry_time=SINCE,
            entry_price=Decimal("100"),
            average_price=Decimal("100.05"),
            exit_settings=EXIT,
        )
        closed = await repo.open(
            conn,
            market_id=other_id,
            entry_time=SINCE,
            entry_price=Decimal("100"),
            average_price=Decimal("100.05"),
            exit_settings=EXIT,
        )
        assert closed is not None
        await repo.close(
            conn,
            position_id=closed.position_id,
            exit_time=SINCE + timedelta(minutes=30),
            exit_price=Decimal("97"),
            exit_reason=Reason.STOP_LOSS,
            net_profit_rate=Decimal("-3.0966"),
        )

    text = await _reporter(database, markets).build(AT, SINCE, "봇 시작(11-23 05:00)")

    assert text.split("\n") == [
        "📊 [상태] 11-23 09:00 · 모의 실행",
        "보유",
        f"  {held} 평단 100 · 현재가 없음",
        "봇 시작(11-23 05:00) 이후",
        "  닫힌 포지션 1건 · 수익률 합계 -3.10%",
        "매수 신호 (9개 조건 중)",
        f"  {held} 0/9 · {other} 0/9",
    ]


@pytest.mark.integration
async def test_no_positions_and_halted_market(database: Database, markets: dict[str, int]) -> None:
    halted = next(iter(markets))
    reporter = _reporter(database, markets, balance=Balance([]), halts=Halts([halted]))

    lines = (await reporter.build(AT, SINCE, "05:00")).split("\n")

    assert lines[1] == f"원화 0원 · 🚨 매매 중단: {halted}"
    assert lines[2] == "보유 없음"
    assert lines[4] == "  체결 0건 · 닫힌 포지션 0건 · 손익 +0원"


@pytest.mark.integration
@pytest.mark.parametrize("error", [UpbitAuthError("401", status=401), UpbitConnectionError("dns")])
async def test_balance_failure_keeps_other_lines(database: Database, markets: dict[str, int], error: Exception) -> None:
    balance = Balance(error)

    lines = (await _reporter(database, markets, balance=balance, halts=Halts([])).build(AT, SINCE, "05:00")).split("\n")

    assert lines[1] == "원화 조회 실패 · 매매 중단 없음"
    assert lines[2] == "보유 없음"
    # 일시적인 실패만 다시 시도한다
    assert balance.calls == (3 if isinstance(error, UpbitConnectionError) else 1)


class BrokenDatabase:
    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        raise OSError("connection refused")
        yield


@pytest.mark.parametrize("error", [OSError("refused"), TimeoutError()])
async def test_database_failure_is_reported(error: Exception, caplog: pytest.LogCaptureFixture) -> None:
    class Broken:
        @asynccontextmanager
        async def transaction(self) -> AsyncIterator[None]:
            raise error
            yield

    reporter = _reporter(Broken(), {"KRW-BTC": 1}, balance=Balance([_krw("1000")]), halts=Halts([]))  # type: ignore[arg-type]

    text = await reporter.build(AT, SINCE, "05:00")

    assert text.split("\n") == [
        "📊 [상태] 11-23 09:00 · 실거래",
        "원화 1,000원 · 매매 중단 없음",
        "DB 조회 실패 (보유·체결·신호를 확인하지 못함)",
    ]
    assert f"status report: database query failed ({type(error).__name__})" in caplog.text


async def test_run_submits_report_at_each_time_and_moves_window() -> None:
    now = [AT - timedelta(minutes=10)]
    waits: list[float] = []
    submitted: list[tuple[str, object]] = []
    notifier = Notifier()

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += timedelta(seconds=seconds)
        if len(waits) == 3:
            raise asyncio.CancelledError

    reporter = StatusReporter(
        database=BrokenDatabase(),  # type: ignore[arg-type]
        market_ids={"KRW-BTC": 1},
        prices=lambda market: None,
        notifier=notifier,
        submit=lambda name, job: submitted.append((name, job)) or True,
        stochastic_entry_max=50.0,
        started_at=AT - timedelta(minutes=30),
        clock=lambda: now[0],
        sleep=sleep,
    )

    built: list[tuple[datetime, datetime, str]] = []

    async def build(at: datetime, since: datetime, label: str) -> str:
        built.append((at, since, label))
        return f"report {len(built)}"

    reporter.build = build  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        await reporter.run()

    # 09:00, 13:00 에 작업을 넘긴다
    assert waits == [600.0, 4 * 3600.0, 4 * 3600.0]
    assert [name for name, _ in submitted] == [JOB_NAME, JOB_NAME]
    for _, job in submitted:
        await job()  # type: ignore[operator]
    # 첫 알림은 봇 시작부터, 다음 알림은 직전 알림 시각부터 센다
    assert built == [(AT, AT - timedelta(minutes=30), "봇 시작(11-23 08:30)"), (AT + timedelta(hours=4), AT, "09:00")]
    assert notifier.messages == ["report 1", "report 2"]
