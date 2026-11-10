"""실거래 판단 (가짜 실행기 + 실제 PostgreSQL). 언제 사고팔지, 얼마를 사고팔지, 언제 판단하지 않는지 확인한다."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio

from src.app.config import ExitSettings, OrderSettings
from src.connections.database import Database
from src.exchange.account import Account, OrderChance
from src.exchange.candle import Candle
from src.exchange.ticker import Ticker
from src.exchange.upbit_client import UpbitServerError
from src.repositories.market import MarketRepository
from src.repositories.models import IndicatorInfo, IndicatorType
from src.repositories.position import PositionRepository
from src.trading.live_trader import LiveTrader, buy_amount, min_buy_amount
from src.trading.reasons import Reason
from src.trading.signals import CrossState, OverState

pytestmark = pytest.mark.integration

T0 = datetime(2026, 11, 1, tzinfo=UTC)
CONFIG_EXIT = ExitSettings(take_profit_rate=Decimal("2"), stop_loss_rate=Decimal("5"), min_profit_rate=Decimal("1"))
HELD_EXIT = ExitSettings(take_profit_rate=Decimal("1.5"), stop_loss_rate=Decimal("3"), min_profit_rate=Decimal("0.5"))


def _chance(market: str, *, ask_fee: str = "0.0005", max_total: str = "1000000000") -> OrderChance:
    return OrderChance(
        market=market,
        bid_fee=Decimal("0.0005"),
        ask_fee=Decimal(ask_fee),
        bid_min_total=Decimal("5000"),
        ask_min_total=Decimal("5000"),
        max_total=Decimal(max_total),
        state="active",
    )


def _krw(balance: str) -> Account:
    return Account(
        currency="KRW", balance=Decimal(balance), locked=Decimal(0), avg_buy_price=Decimal(0), unit_currency="KRW"
    )


def _entry_states(market_id: int, candle_time: datetime) -> dict[IndicatorType, IndicatorInfo]:
    """신규 매수 9개 조건을 모두 만족하는 상태 (매매 로직 5.6)."""

    def info(indicator_type: IndicatorType, over: OverState | None) -> IndicatorInfo:
        return IndicatorInfo(
            market_id=market_id,
            indicator_type=indicator_type,
            value=20.0,
            signal=20.0,
            candle_time=candle_time,
            over_state=over,
            over_time=candle_time if over else None,
            cross_state=CrossState.GOLDEN_CROSS,
            cross_time=candle_time,
        )

    return {
        IndicatorType.RSI: info(IndicatorType.RSI, OverState.OVER_SOLD),
        IndicatorType.STOCHASTIC: info(IndicatorType.STOCHASTIC, OverState.OVER_SOLD),
        IndicatorType.MACD_SHORT: info(IndicatorType.MACD_SHORT, None),
        IndicatorType.MACD_MID: info(IndicatorType.MACD_MID, None),
        IndicatorType.MACD_LONG: info(IndicatorType.MACD_LONG, None),
    }


def _candle(market: str, close: str, start: datetime = T0) -> Candle:
    price = Decimal(close)
    return Candle(market=market, start=start, open=price, high=price, low=price, close=price)


class FakeExecutor:
    def __init__(self) -> None:
        self.halted: set[str] = set()
        self.unfinished: set[str] = set()
        self.resolved: list[str] = []
        self.buys: list[tuple[str, Decimal]] = []
        self.buy_reasons: list[Reason] = []
        self.sells: list[tuple[str, Decimal, Reason]] = []
        self.on_buy: asyncio.Event | None = None

    def is_halted(self, market: str) -> bool:
        return market in self.halted

    async def resolve_unfinished(self, market: str, market_id: int) -> bool:
        self.resolved.append(market)
        return market in self.unfinished

    async def buy(self, market: str, market_id: int, amount: Decimal, *, reason: Reason = Reason.ENTRY) -> None:
        self.buys.append((market, amount))
        self.buy_reasons.append(reason)
        if self.on_buy is not None:
            await self.on_buy.wait()

    async def sell(self, market: str, market_id: int, volume: Decimal, reason: Reason) -> None:
        self.sells.append((market, volume, reason))


class FakeAccounts:
    def __init__(self, balance: str = "123456.78") -> None:
        self.balance = balance
        self.error: Exception | None = None
        self.calls = 0

    async def get_accounts(self) -> list[Account]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return [_krw(self.balance)]


class Env:
    def __init__(self, database: Database, markets: Mapping[str, int]) -> None:
        self.database = database
        self.markets = markets
        self.market, self.market_id = next(iter(markets.items()))
        self.executor = FakeExecutor()
        self.accounts = FakeAccounts()
        self.chances = {market: _chance(market) for market in markets}
        # 매수 비율 10%, 추가 매수는 첫 매수 금액의 1.5배로 최대 2번 (매매 로직 5.6)
        self.order = OrderSettings(buy_ratio=Decimal("10"), add_buy_multiple=Decimal("1.5"), max_add_buys=2)
        self.trader = self.make_trader()

    def make_trader(self) -> LiveTrader:
        return LiveTrader(
            executor=self.executor,  # type: ignore[arg-type]
            upbit=self.accounts,
            database=self.database,
            chances=self.chances,
            order_settings=self.order,
            exit_settings=CONFIG_EXIT,
            stochastic_entry_max=50.0,
        )

    async def hold(self, *, volume: str = "0.001", cost: str = "100000", buys: int = 1) -> None:
        """평단가 100,000,000원짜리 포지션을 매수할 때의 기준(HELD_EXIT)으로 연다."""
        async with self.database.transaction() as conn:
            await PositionRepository().open(
                conn,
                market_id=self.market_id,
                volume=Decimal(volume),
                cost=Decimal(cost),
                opened_at=T0,
                exit_settings=HELD_EXIT,
                first_buy_amount=Decimal("10000"),
            )
            # 추가 매수를 한 포지션으로 만든다
            await conn.execute("UPDATE position SET buy_count = $2 WHERE market_id = $1", self.market_id, buys)

    async def candle(self, close: str, *, allow_buy: bool = True, start: datetime = T0) -> None:
        await self.trader.on_candle(
            self.market,
            self.market_id,
            candle=_candle(self.market, close, start),
            states=_entry_states(self.market_id, start),
            allow_buy=allow_buy,
        )

    async def tick(self, price: str, seconds: int = 1) -> None:
        ticker = Ticker(market=self.market, price=Decimal(price), traded_at=T0 + timedelta(seconds=seconds))
        await self.trader.on_ticker(self.market, self.market_id, ticker)


@pytest_asyncio.fixture
async def env(database: Database) -> AsyncIterator[Env]:
    symbols = ["KRW-T" + uuid.uuid4().hex[:8].upper() for _ in range(2)]
    markets = {}
    async with database.transaction() as conn:
        for symbol in symbols:
            market = await MarketRepository().add(conn, symbol)
            assert market is not None
            markets[symbol] = market.market_id
    try:
        yield Env(database, markets)
    finally:
        async with database.transaction() as conn:
            await conn.execute("DELETE FROM position WHERE market_id = ANY($1::int[])", list(markets.values()))
            await conn.execute("DELETE FROM market WHERE market_id = ANY($1::int[])", list(markets.values()))


@pytest.mark.parametrize(
    ("balance", "ratio", "max_total", "expected"),
    [
        # 원 단위로 내린다 (매매 로직 6.1)
        ("123456.78", "10", "1000000000", "12345"),
        ("50000", "10", "1000000000", "5000"),
        ("49999", "10", "1000000000", "4999"),
        ("0", "10", "1000000000", "0"),
        # 거래소 최대 주문 금액을 넘지 않는다
        ("100000000000", "50", "1000000000", "1000000000"),
    ],
)
def test_buy_amount(balance: str, ratio: str, max_total: str, expected: str) -> None:
    amount = buy_amount(balance=Decimal(balance), buy_ratio=Decimal(ratio), max_total=Decimal(max_total))

    assert amount == Decimal(expected)


async def test_entry_signal_buys_ratio_of_krw_balance(env: Env) -> None:
    await env.candle("100000000")

    assert env.executor.buys == [(env.market, Decimal("12345"))]
    assert env.accounts.calls == 1


async def test_no_buy_when_buying_not_allowed(env: Env) -> None:
    # 계산 구간이 짧은 캔들에서는 매수하지 않는다 (매매 로직 3.0)
    await env.candle("100000000", allow_buy=False)

    assert env.executor.buys == []
    assert env.accounts.calls == 0


@pytest.mark.parametrize(("balance", "bought"), [("99999", None), ("100000", "10000")])
async def test_buy_needs_twice_minimum_order(env: Env, balance: str, bought: str | None) -> None:
    # 잔고의 10%가 최소 주문 금액(5,000원)의 2배 이상일 때만 산다. 가격이 떨어져도 손절 매도가 최소 금액에 걸리지 않게 한다
    env.accounts.balance = balance

    await env.candle("100000000")

    assert env.executor.buys == ([] if bought is None else [(env.market, Decimal(bought))])


def test_min_buy_amount_uses_larger_of_bid_and_ask_minimum() -> None:
    chance = replace(_chance("KRW-BTC"), ask_min_total=Decimal("6000"))

    assert min_buy_amount(_chance("KRW-BTC")) == Decimal("10000")
    assert min_buy_amount(chance) == Decimal("12000")


async def test_no_buy_when_balance_unavailable(env: Env) -> None:
    env.accounts.error = UpbitServerError("GET /v1/accounts failed (503)", status=503)

    await env.candle("100000000")

    # 잔고를 모르면 사지 않는다
    assert env.executor.buys == []


async def test_no_entry_signal_no_buy(env: Env) -> None:
    await env.trader.on_candle(
        env.market, env.market_id, candle=_candle(env.market, "100000000"), states={}, allow_buy=True
    )

    assert env.executor.buys == []
    assert env.accounts.calls == 0


async def test_halted_market_is_not_judged(env: Env) -> None:
    env.executor.halted.add(env.market)

    await env.candle("100000000")
    await env.hold()
    await env.tick("90000000")

    assert (env.executor.buys, env.executor.sells, env.executor.resolved) == ([], [], [])


async def test_unfinished_order_is_resolved_and_not_judged(env: Env) -> None:
    env.executor.unfinished.add(env.market)

    await env.candle("100000000")
    await env.hold()
    await env.tick("90000000")

    # 끝나지 않은 주문이 있으면 결과만 확인하고 판단하지 않는다 (매매 로직 5.1)
    assert env.executor.resolved == [env.market, env.market]
    assert (env.executor.buys, env.executor.sells) == ([], [])


async def test_holding_never_buys_as_new_entry(env: Env) -> None:
    await env.hold()

    await env.candle("100000000")

    # 보유 중의 매수는 추가 매수뿐이다. 포지션을 새로 열지 않는다
    assert env.executor.buy_reasons == [Reason.ADD]


async def test_candle_stop_loss_sells_all_with_held_settings(env: Env) -> None:
    await env.hold(volume="0.0012345", cost="123450")

    # 순수익률 약 -3.05%. 매수할 때의 기준(손절 3%)으로 판단한다. 설정의 기준(5%)이면 팔지 않는다
    await env.candle("97000000")

    assert env.executor.sells == [(env.market, Decimal("0.0012345"), Reason.STOP_LOSS)]


async def test_realtime_take_profit(env: Env) -> None:
    await env.hold()

    await env.tick("101600000")

    assert env.executor.sells == [(env.market, Decimal("0.001"), Reason.TAKE_PROFIT)]
    assert env.executor.buys == []


async def test_realtime_uses_exchange_sell_fee(env: Env) -> None:
    await env.hold()
    # 101,600,000원은 수수료 0.05%면 순수익률 약 1.55%로 익절이지만, 0.2%면 약 1.40%로 익절이 아니다
    env.chances[env.market] = _chance(env.market, ask_fee="0.002")
    env.trader = env.make_trader()

    await env.tick("101600000")

    assert env.executor.sells == []


async def test_realtime_without_position_does_nothing(env: Env) -> None:
    await env.tick("1")

    assert (env.executor.buys, env.executor.sells) == ([], [])


async def test_realtime_ignores_price_before_buy_fill(env: Env) -> None:
    await env.hold()

    await env.tick("90000000", seconds=0)

    assert env.executor.sells == []


async def test_buys_in_different_markets_run_one_at_a_time(env: Env) -> None:
    other, other_id = list(env.markets.items())[1]
    env.executor.on_buy = asyncio.Event()

    first = asyncio.create_task(env.candle("100000000"))
    await asyncio.sleep(0.05)
    second = asyncio.create_task(
        env.trader.on_candle(
            other, other_id, candle=_candle(other, "100000000"), states=_entry_states(other_id, T0), allow_buy=True
        )
    )
    await asyncio.sleep(0.05)

    # 앞선 매수가 끝나기 전에는 다음 마켓이 잔고를 보지 않는다 (매매 로직 6.1)
    assert env.accounts.calls == 1
    assert [market for market, _ in env.executor.buys] == [env.market]

    env.executor.on_buy.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=5)
    assert env.accounts.calls == 2
    assert [market for market, _ in env.executor.buys] == [env.market, other]


# ---------------------------------------------------------------------------
# 추가 매수 (매매 로직 5.6)
# ---------------------------------------------------------------------------


async def test_add_buy_is_first_amount_times_multiple(env: Env) -> None:
    # 첫 매수 10,000원인 포지션을 들고 있는데 매수 신호가 다시 났다. 손절·익절 범위 안이다
    await env.hold()

    await env.candle("99000000")

    assert env.executor.buys == [(env.market, Decimal("15000"))]
    assert env.executor.buy_reasons == [Reason.ADD]


async def test_no_add_buy_after_limit(env: Env) -> None:
    await env.hold(buys=3)

    await env.candle("99000000")

    # 첫 매수 1번 + 추가 매수 2번을 했으면 더 사지 않는다
    assert env.executor.buys == []
    assert env.accounts.calls == 0


async def test_no_add_buy_when_disabled(env: Env) -> None:
    env.order = OrderSettings(buy_ratio=Decimal("10"), max_add_buys=0)
    env.trader = env.make_trader()
    await env.hold()

    await env.candle("99000000")

    assert env.executor.buys == []


async def test_no_add_buy_when_balance_too_low(env: Env) -> None:
    await env.hold()
    # 15,000원 + 수수료 7.5원보다 1원 모자란다
    env.accounts.balance = "15006.5"

    await env.candle("99000000")

    # 금액을 줄여서 사지 않는다
    assert env.executor.buys == []


async def test_add_buy_with_exact_balance(env: Env) -> None:
    await env.hold()
    env.accounts.balance = "15007.5"

    await env.candle("99000000")

    assert env.executor.buys == [(env.market, Decimal("15000"))]


async def test_no_add_buy_when_balance_unavailable(env: Env) -> None:
    await env.hold()
    env.accounts.error = UpbitServerError("GET /v1/accounts failed (503)", status=503)

    await env.candle("99000000")

    assert env.executor.buys == []


async def test_no_add_buy_with_short_window(env: Env) -> None:
    await env.hold()

    await env.candle("99000000", allow_buy=False)

    assert env.executor.buys == []


async def test_no_add_buy_below_minimum(env: Env) -> None:
    # 첫 매수 금액이 6,000원이면 추가 매수는 9,000원으로 최소 금액(10,000원)보다 작다
    await env.hold()
    async with env.database.transaction() as conn:
        await conn.execute("UPDATE position SET first_buy_amount = 6000 WHERE market_id = $1", env.market_id)

    await env.candle("99000000")

    assert env.executor.buys == []
    assert env.accounts.calls == 0


async def test_stop_loss_comes_before_add_buy(env: Env) -> None:
    await env.hold()

    # 매수 신호가 나도 손절 기준(-3%) 아래면 먼저 판다
    await env.candle("96000000")

    assert env.executor.buys == []
    assert env.executor.sells == [(env.market, Decimal("0.001"), Reason.STOP_LOSS)]
