"""실거래 주문 실행 (가짜 거래소 + 실제 PostgreSQL).

정상 체결뿐 아니라 거절, 응답 없음, 체결 지연, 거래소와 기록 불일치에서 주문을 다시 보내지 않고
안전한 상태(끝나지 않은 주문으로 남거나 매매 중단)로 남는지 확인한다.
"""

import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio

from src.app.config import ExitSettings
from src.connections.database import Database
from src.exchange.order import Order, OrderSide, OrderState, OrderType, Trade
from src.exchange.upbit_client import (
    UpbitRequestError,
    UpbitServerError,
    UpbitServerResultUnknownError,
    UpbitTimeoutError,
)
from src.repositories.indicator_info import IndicatorInfoRepository
from src.repositories.market import MarketRepository
from src.repositories.models import (
    IndicatorInfo,
    IndicatorType,
    LiveOrder,
    LiveOrderState,
    LivePosition,
    PositionStatus,
)
from src.repositories.order import OrderRepository
from src.repositories.position import PositionRepository
from src.trading.live import NOT_FOUND_GRACE, LiveExecutor
from src.trading.reasons import Reason
from src.trading.signals import CrossState

pytestmark = pytest.mark.integration

T0 = datetime(2026, 11, 1, tzinfo=UTC)
EXIT = ExitSettings(take_profit_rate=Decimal("1.5"), stop_loss_rate=Decimal("3"), min_profit_rate=Decimal("0.5"))
MISSING = object()  # 거래소에 주문이 없음 (get_order가 None)


def _trade(price: str, volume: str, funds: str, seconds: int = 0) -> Trade:
    return Trade(
        price=Decimal(price), volume=Decimal(volume), funds=Decimal(funds), created_at=T0 + timedelta(seconds=seconds)
    )


def _order(
    market: str,
    identifier: str,
    side: OrderSide,
    state: OrderState,
    trades: tuple[Trade, ...] = (),
    paid_fee: str = "0",
) -> Order:
    return Order(
        uuid=str(uuid.uuid4()),
        identifier=identifier,
        market=market,
        side=side,
        ord_type=OrderType.PRICE if side is OrderSide.BID else OrderType.MARKET,
        state=state,
        created_at=T0,
        price=None,
        volume=None,
        executed_volume=sum((t.volume for t in trades), Decimal(0)),
        paid_fee=Decimal(paid_fee),
        trades_count=len(trades),
        trades=trades,
    )


class FakeUpbit:
    """주문 생성 결과와 주문 조회 결과를 차례로 돌려준다. 조회 결과가 다 떨어지면 마지막 결과를 계속 돌려준다."""

    def __init__(self, market: str) -> None:
        self.market = market
        self.placed: list[tuple[OrderSide, Decimal, str]] = []
        self.place_error: Exception | None = None
        # get_order가 차례로 돌려줄 것: (side, state, trades, fee) 또는 MISSING 또는 예외
        self.lookups: list[object] = []
        self.looked_up: list[str] = []

    async def place_market_buy(self, market: str, krw_amount: Decimal, identifier: str) -> Order:
        return self._place(OrderSide.BID, krw_amount, identifier)

    async def place_market_sell(self, market: str, volume: Decimal, identifier: str) -> Order:
        return self._place(OrderSide.ASK, volume, identifier)

    def _place(self, side: OrderSide, quantity: Decimal, identifier: str) -> Order:
        self.placed.append((side, quantity, identifier))
        if self.place_error is not None:
            raise self.place_error
        return _order(self.market, identifier, side, OrderState.WAIT)

    async def get_order(self, identifier: str) -> Order | None:
        self.looked_up.append(identifier)
        result = self.lookups.pop(0) if len(self.lookups) > 1 else self.lookups[0]
        if result is MISSING:
            return None
        if isinstance(result, Exception):
            raise result
        if callable(result):
            return result(identifier)
        side, state, trades, fee = result  # type: ignore[misc]
        return _order(self.market, identifier, side, state, trades, fee)


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now


class Env:
    def __init__(self, database: Database, market: str, market_id: int) -> None:
        self.database = database
        self.market = market
        self.market_id = market_id
        self.upbit = FakeUpbit(market)
        self.clock = Clock()
        self.sleeps: list[float] = []

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        self.executor = LiveExecutor(
            upbit=self.upbit,
            database=database,
            exit_settings=EXIT,
            ask_min_totals={market: Decimal("5000")},
            sleep=sleep,
            clock=self.clock,
        )

    async def unfinished(self) -> LiveOrder | None:
        async with self.database.transaction() as conn:
            return await OrderRepository().get_unfinished(conn, self.market_id)

    async def position(self) -> LivePosition | None:
        async with self.database.transaction() as conn:
            return await PositionRepository().get_open(conn, self.market_id)

    async def open_position(self, volume: str = "0.001", cost: str = "5002.5") -> None:
        async with self.database.transaction() as conn:
            await PositionRepository().open(
                conn,
                market_id=self.market_id,
                volume=Decimal(volume),
                cost=Decimal(cost),
                opened_at=T0 - timedelta(hours=1),
                exit_settings=EXIT,
                first_buy_amount=Decimal("5000"),
            )

    async def set_signal(self) -> None:
        async with self.database.transaction() as conn:
            await IndicatorInfoRepository().save(
                conn,
                IndicatorInfo(
                    market_id=self.market_id,
                    indicator_type=IndicatorType.RSI,
                    value=30.0,
                    signal=29.0,
                    candle_time=T0,
                    cross_state=CrossState.GOLDEN_CROSS,
                    cross_time=T0,
                ),
            )

    async def signal(self) -> CrossState | None:
        async with self.database.transaction() as conn:
            infos = await IndicatorInfoRepository().list_by_market(conn, self.market_id)
        return infos[IndicatorType.RSI].cross_state


@pytest_asyncio.fixture
async def env(database: Database) -> AsyncIterator[Env]:
    """마켓 하나를 만들고, 시험이 끝나면 그 마켓의 데이터를 지운다 (실행기는 자체 트랜잭션으로 커밋한다)."""
    symbol = "KRW-T" + uuid.uuid4().hex[:8].upper()
    async with database.transaction() as conn:
        market = await MarketRepository().add(conn, symbol)
    assert market is not None
    try:
        yield Env(database, symbol, market.market_id)
    finally:
        async with database.transaction() as conn:
            await conn.execute("DELETE FROM orders WHERE market_id = $1", market.market_id)
            await conn.execute("DELETE FROM position WHERE market_id = $1", market.market_id)
            await conn.execute("DELETE FROM indicator_info WHERE market_id = $1", market.market_id)
            await conn.execute("DELETE FROM market WHERE market_id = $1", market.market_id)


BUY_FILL = (_trade("5000000", "0.0006", "3000", 0), _trade("5000100", "0.0004", "2000.04", 1))


async def test_buy_fills_and_opens_position(env: Env) -> None:
    await env.set_signal()
    env.upbit.lookups = [(OrderSide.BID, OrderState.DONE, BUY_FILL, "2.50002")]

    order = await env.executor.buy(env.market, env.market_id, Decimal("5000.04"))

    assert order is not None
    assert (order.state, order.executed_volume, order.executed_funds, order.paid_fee) == (
        LiveOrderState.DONE,
        Decimal("0.001"),
        Decimal("5000.04"),
        Decimal("2.50002"),
    )
    assert env.upbit.placed == [(OrderSide.BID, Decimal("5000.04"), order.identifier)]
    position = await env.position()
    # 평단가는 매수 수수료를 포함한 실제 체결 기준이다 (매매 로직 5.2). 열린 시각은 마지막 체결 시각이다
    assert (position.entry_volume, position.entry_cost, position.opened_at) == (
        Decimal("0.001"),
        Decimal("5002.54002"),
        T0 + timedelta(seconds=1),
    )
    assert (position.take_profit_rate, position.stop_loss_rate) == (Decimal("1.5"), Decimal("3"))
    # 체결된 뒤에 신호 상태를 비운다 (매매 로직 7)
    assert await env.signal() is None
    assert await env.unfinished() is None


async def test_full_sell_closes_position(env: Env) -> None:
    await env.open_position()
    env.upbit.lookups = [(OrderSide.ASK, OrderState.DONE, (_trade("5100000", "0.001", "5100"),), "2.55")]

    order = await env.executor.sell(env.market, env.market_id, Decimal("0.001"), Reason.TAKE_PROFIT)

    assert order is not None and order.state is LiveOrderState.DONE
    assert await env.position() is None
    async with env.database.transaction() as conn:
        row = await conn.fetchrow("SELECT * FROM position WHERE market_id = $1", env.market_id)
    assert (row["status"], row["sold_volume"], row["sold_proceeds"], row["exit_reason"]) == (
        PositionStatus.CLOSED,
        Decimal("0.001"),
        Decimal("5097.45"),
        Reason.TAKE_PROFIT,
    )


async def test_partial_sell_keeps_sellable_remainder(env: Env) -> None:
    await env.open_position(volume="0.002", cost="10005")
    # 시장가 매도가 일부만 체결되고 남은 양이 취소됐다. 남은 0.0015개(약 7,650원)는 더 팔 수 있다
    env.upbit.lookups = [(OrderSide.ASK, OrderState.CANCEL, (_trade("5100000", "0.0005", "2550"),), "1.275")]

    order = await env.executor.sell(env.market, env.market_id, Decimal("0.002"), Reason.STOP_LOSS)

    assert order is not None and order.state is LiveOrderState.CANCEL
    position = await env.position()
    assert (position.status, position.holding_volume) == (PositionStatus.OPEN, Decimal("0.0015"))


async def test_partial_sell_closes_unsellable_remainder(env: Env) -> None:
    await env.open_position(volume="0.002", cost="10005")
    # 남은 0.0005개는 2,550원으로 최소 주문 금액(5,000원)보다 작아 팔 수 없다
    env.upbit.lookups = [(OrderSide.ASK, OrderState.CANCEL, (_trade("5100000", "0.0015", "7650"),), "3.825")]

    await env.executor.sell(env.market, env.market_id, Decimal("0.002"), Reason.STOP_LOSS)

    assert await env.position() is None


async def test_rejected_order_is_failed_and_halts_market(env: Env) -> None:
    await env.set_signal()
    env.upbit.place_error = UpbitRequestError(
        "POST /v1/orders failed (400 insufficient_funds_bid)", status=400, name="insufficient_funds_bid"
    )

    order = await env.executor.buy(env.market, env.market_id, Decimal("5000"))

    assert order is not None and order.state is LiveOrderState.FAILED
    assert await env.position() is None
    # 체결되지 않았으므로 신호 상태는 그대로다
    assert await env.signal() is CrossState.GOLDEN_CROSS
    # 같은 주문이 매번 거절되지 않게 사람이 확인할 때까지 이 마켓은 주문하지 않는다
    assert env.executor.is_halted(env.market)
    assert await env.executor.buy(env.market, env.market_id, Decimal("5000")) is None
    assert await env.executor.resolve_unfinished(env.market, env.market_id) is True
    assert len(env.upbit.placed) == 1


@pytest.mark.parametrize(
    "error",
    [
        UpbitTimeoutError("POST /v1/orders timed out"),
        UpbitServerResultUnknownError("POST /v1/orders failed (500)", status=500),
    ],
)
async def test_unknown_result_is_confirmed_by_identifier_not_resent(env: Env, error: Exception) -> None:
    env.upbit.place_error = error
    env.upbit.lookups = [(OrderSide.BID, OrderState.DONE, BUY_FILL, "2.50002")]

    order = await env.executor.buy(env.market, env.market_id, Decimal("5000.04"))

    # 응답을 못 받았어도 다시 보내지 않고 identifier로 조회해 체결을 반영한다
    assert order is not None and order.state is LiveOrderState.DONE
    assert len(env.upbit.placed) == 1
    assert env.upbit.looked_up == [order.identifier]
    assert (await env.position()).entry_volume == Decimal("0.001")
    assert not env.executor.is_halted(env.market)


async def test_not_found_soon_after_sending_stays_pending(env: Env) -> None:
    env.upbit.place_error = UpbitTimeoutError("POST /v1/orders timed out")
    env.upbit.lookups = [MISSING]

    order = await env.executor.buy(env.market, env.market_id, Decimal("5000"))

    # 막 보낸 주문은 거래소에 아직 반영되지 않았을 수 있어 실패로 단정하지 않는다
    assert order is not None and order.state is LiveOrderState.PENDING
    assert await env.unfinished() == order
    # 끝나지 않은 주문이 있으면 새 주문을 내지 않는다
    assert await env.executor.buy(env.market, env.market_id, Decimal("5000")) is None
    assert len(env.upbit.placed) == 1


async def test_not_found_after_grace_is_failed_and_allows_next_order(env: Env) -> None:
    env.upbit.place_error = UpbitTimeoutError("POST /v1/orders timed out")
    env.upbit.lookups = [MISSING]
    order = await env.executor.buy(env.market, env.market_id, Decimal("5000"))
    assert order is not None

    env.clock.now = order.created_at + NOT_FOUND_GRACE
    assert await env.executor.resolve_unfinished(env.market, env.market_id) is True

    assert await env.unfinished() is None
    assert not env.executor.is_halted(env.market)


async def test_pending_order_found_later_is_applied(env: Env) -> None:
    env.upbit.place_error = UpbitTimeoutError("POST /v1/orders timed out")
    env.upbit.lookups = [MISSING]
    order = await env.executor.buy(env.market, env.market_id, Decimal("5000.04"))
    assert order is not None

    # 재시작 뒤 등 다음 작업에서 확인했더니 체결돼 있었다
    env.upbit.lookups = [(OrderSide.BID, OrderState.DONE, BUY_FILL, "2.50002")]
    assert await env.executor.resolve_unfinished(env.market, env.market_id) is True

    assert await env.unfinished() is None
    assert (await env.position()).entry_volume == Decimal("0.001")


async def test_order_still_waiting_is_checked_again_later(env: Env) -> None:
    env.upbit.lookups = [(OrderSide.BID, OrderState.WAIT, (), "0")]

    order = await env.executor.buy(env.market, env.market_id, Decimal("5000.04"))

    assert order is not None and order.state is LiveOrderState.WAIT
    # 정해진 횟수만큼만 확인하고 기다린다 (무한 대기 없음)
    assert len(env.upbit.looked_up) == 5
    assert env.sleeps == [1.0] * 4

    env.upbit.lookups = [(OrderSide.BID, OrderState.DONE, BUY_FILL, "2.50002")]
    assert await env.executor.resolve_unfinished(env.market, env.market_id) is True
    assert (await env.position()).entry_volume == Decimal("0.001")


async def test_lookup_failure_leaves_order_unfinished(env: Env) -> None:
    env.upbit.lookups = [UpbitServerError("GET /v1/order failed (503)", status=503)]

    order = await env.executor.buy(env.market, env.market_id, Decimal("5000"))

    assert order is not None and order.state is LiveOrderState.PENDING
    assert await env.unfinished() == order
    assert not env.executor.is_halted(env.market)


async def test_resolve_without_unfinished_order(env: Env) -> None:
    assert await env.executor.resolve_unfinished(env.market, env.market_id) is False
    assert env.upbit.looked_up == []


def _no_trades(market: str) -> Callable[[str], Order]:
    # 체결 수량은 있는데 체결 목록이 없는 응답. 체결 금액을 알 수 없다
    return lambda identifier: replace(
        _order(market, identifier, OrderSide.BID, OrderState.DONE), executed_volume=Decimal("0.001")
    )


@pytest.mark.parametrize("case", ["no_trades", "trades_short", "wrong_side", "wrong_market"])
async def test_inconsistent_result_halts_without_recording(env: Env, case: str) -> None:
    await env.set_signal()
    env.upbit.lookups = [
        {
            "no_trades": _no_trades(env.market),
            # 체결 목록 일부가 빠져 합계가 체결 수량보다 작다. 이대로 반영하면 평단가가 틀린다
            "trades_short": lambda identifier: replace(
                _order(env.market, identifier, OrderSide.BID, OrderState.DONE, BUY_FILL[:1]),
                executed_volume=Decimal("0.001"),
            ),
            "wrong_side": (OrderSide.ASK, OrderState.DONE, BUY_FILL, "2.50002"),
            "wrong_market": lambda identifier: _order(
                "KRW-OTHER", identifier, OrderSide.BID, OrderState.DONE, BUY_FILL
            ),
        }[case]
    ]

    order = await env.executor.buy(env.market, env.market_id, Decimal("5000.04"))

    # 반영하지 않고 끝나지 않은 주문으로 남겨, 사람이 확인할 때까지 이 마켓은 주문하지 않는다
    assert env.executor.is_halted(env.market)
    assert order is not None and order.state is LiveOrderState.PENDING
    assert await env.unfinished() == order
    assert await env.position() is None
    assert await env.signal() is CrossState.GOLDEN_CROSS


async def test_buy_fill_with_position_already_open_halts(env: Env) -> None:
    env.upbit.lookups = [(OrderSide.BID, OrderState.DONE, BUY_FILL, "2.50002")]
    await env.open_position()

    order = await env.executor.buy(env.market, env.market_id, Decimal("5000.04"))

    # 한 트랜잭션이라 주문 결과도 기록되지 않는다
    assert env.executor.is_halted(env.market)
    assert order is not None and await env.unfinished() == order


async def test_sell_fill_without_position_halts(env: Env) -> None:
    env.upbit.lookups = [(OrderSide.ASK, OrderState.DONE, (_trade("5100000", "0.001", "5100"),), "2.55")]

    await env.executor.sell(env.market, env.market_id, Decimal("0.001"), Reason.STOP_LOSS)

    assert env.executor.is_halted(env.market)
    assert await env.unfinished() is not None


async def test_cancelled_without_fill_records_nothing_else(env: Env) -> None:
    await env.set_signal()
    env.upbit.lookups = [(OrderSide.BID, OrderState.CANCEL, (), "0")]

    order = await env.executor.buy(env.market, env.market_id, Decimal("5000"))

    assert order is not None and order.state is LiveOrderState.CANCEL
    assert await env.position() is None
    assert await env.signal() is CrossState.GOLDEN_CROSS
    assert not env.executor.is_halted(env.market)
