import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest

from src.app.config import AddBuyBase, AddBuyTrigger, ExitSettings, OrderSettings, StopLossBy
from src.exchange.candle import Candle
from src.exchange.ticker import Ticker
from src.repositories.indicator_info import IndicatorInfoRepository
from src.repositories.market import MarketRepository
from src.repositories.models import IndicatorInfo, IndicatorType, PositionStatus
from src.repositories.paper_position import PaperPositionRepository
from src.trading.decision import Action, Decision, Reason
from src.trading.paper import (
    PAPER_FEE_RATE,
    PaperTrade,
    apply_paper_decision,
    apply_realtime_exit,
    paper_average_after_add,
)
from src.trading.signals import CrossState, OverState

pytestmark = pytest.mark.integration

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = T0 + timedelta(minutes=30)
# 추가 매수를 하지 않는 기존 설정 (매매 로직 5.6의 max_add_buys = 0)
NO_ADD = OrderSettings(max_add_buys=0)
infos = IndicatorInfoRepository()
positions = PaperPositionRepository()


async def _market_id(conn: asyncpg.Connection) -> int:
    market = await MarketRepository().add(conn, "KRW-T" + uuid.uuid4().hex[:8].upper())
    assert market is not None
    return market.market_id


def _candle(start: datetime, close: str) -> Candle:
    price = Decimal(close)
    return Candle(market="KRW-BTC", start=start, open=price, high=price, low=price, close=price)


async def _save_states(
    conn: asyncpg.Connection, market_id: int, candle_time: datetime, *, entry: bool = False, overbought: bool = False
) -> dict[IndicatorType, IndicatorInfo]:
    """판단할 캔들의 신호 상태를 저장한다. entry면 신규 매수 9개 조건이 모두 참, overbought면 과매수 매도 조건."""
    states = {}
    for indicator_type in IndicatorType:
        info = IndicatorInfo(
            market_id=market_id, indicator_type=indicator_type, value=40.0, signal=30.0, candle_time=candle_time
        )
        is_rsi_or_stochastic = indicator_type in (IndicatorType.RSI, IndicatorType.STOCHASTIC)
        if entry:
            info = replace(info, cross_state=CrossState.GOLDEN_CROSS, cross_time=candle_time)
            if is_rsi_or_stochastic:
                info = replace(info, over_state=OverState.OVER_SOLD, over_time=candle_time)
        if overbought and is_rsi_or_stochastic:
            info = replace(info, over_state=OverState.OVER_BOUGHT, over_time=candle_time)
        assert await infos.save(conn, info) is not None
        states[indicator_type] = info
    return states


async def _apply(
    conn: asyncpg.Connection,
    market_id: int,
    candle: Candle,
    states: dict[IndicatorType, IndicatorInfo],
    *,
    allow_buy: bool = True,
    order_settings: OrderSettings = NO_ADD,
) -> PaperTrade | None:
    return await apply_paper_decision(
        conn,
        market_id=market_id,
        candle=candle,
        states=states,
        exit_settings=ExitSettings(),
        stochastic_entry_max=50.0,
        allow_buy=allow_buy,
        order_settings=order_settings,
    )


def _all_cleared(saved: dict[IndicatorType, IndicatorInfo]) -> bool:
    return all(
        (info.over_state, info.over_time, info.cross_state, info.cross_time) == (None, None, None, None)
        for info in saved.values()
    )


async def test_buy_records_position_and_clears_signal_states(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    states = await _save_states(conn, market_id, T0, entry=True)

    trade = await _apply(conn, market_id, _candle(T0, "100"), states)

    assert trade is not None
    assert (trade.decision.action, trade.decision.reason) == (Action.BUY, Reason.ENTRY)
    # 종가로 체결됐다고 보고, 평단가는 매수 수수료를 포함한다
    assert (trade.position.entry_time, trade.position.entry_price) == (T0, Decimal("100"))
    assert trade.position.average_price == Decimal("100") * (1 + PAPER_FEE_RATE)
    assert await positions.get_open(conn, market_id) == trade.position
    # 매수하면 신호 상태를 모두 비우고 지표 값은 남긴다 (매매 로직 7)
    saved = await infos.list_by_market(conn, market_id)
    assert _all_cleared(saved)
    assert saved[IndicatorType.RSI].value == 40.0


async def test_no_buy_when_not_allowed(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    states = await _save_states(conn, market_id, T0, entry=True)

    assert await _apply(conn, market_id, _candle(T0, "100"), states, allow_buy=False) is None
    assert await positions.get_open(conn, market_id) is None
    # 매수하지 않으면 신호 상태도 바뀌지 않는다
    assert (await infos.list_by_market(conn, market_id)) == states


async def test_no_decision_changes_nothing(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    states = await _save_states(conn, market_id, T0)

    assert await _apply(conn, market_id, _candle(T0, "100"), states) is None
    assert await positions.get_open(conn, market_id) is None
    assert (await infos.list_by_market(conn, market_id)) == states


async def _holding(conn: asyncpg.Connection, market_id: int) -> None:
    await positions.open(
        conn,
        market_id=market_id,
        entry_time=T0,
        entry_price=Decimal("100"),
        average_price=Decimal("100.05"),
        exit_settings=ExitSettings(),
    )


async def test_stop_loss_closes_position(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    await _holding(conn, market_id)
    states = await _save_states(conn, market_id, T1, overbought=True)

    trade = await _apply(conn, market_id, _candle(T1, "96"), states)

    assert trade is not None
    assert (trade.decision.action, trade.decision.reason) == (Action.SELL, Reason.STOP_LOSS)
    position = trade.position
    assert (position.status, position.exit_time, position.exit_price, position.exit_reason) == (
        PositionStatus.CLOSED,
        T1,
        Decimal("96"),
        Reason.STOP_LOSS,
    )
    # (96 * 0.9995 - 100.05) / 100.05 * 100
    expected = (Decimal("96") * (1 - PAPER_FEE_RATE) - Decimal("100.05")) / Decimal("100.05") * 100
    assert position.net_profit_rate == expected
    assert await positions.get_open(conn, market_id) is None
    assert _all_cleared(await infos.list_by_market(conn, market_id))


async def test_overbought_sell_closes_position(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    await _holding(conn, market_id)
    states = await _save_states(conn, market_id, T1, overbought=True)

    trade = await _apply(conn, market_id, _candle(T1, "101"), states)

    assert trade is not None
    assert (trade.decision.action, trade.decision.reason) == (Action.SELL, Reason.OVERBOUGHT_EXIT)
    assert trade.position.net_profit_rate is not None and trade.position.net_profit_rate > 0


async def test_holding_without_sell_condition_keeps_position(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    await _holding(conn, market_id)
    # 매수 조건이 모두 참이어도 보유 중이면 다시 사지 않는다
    states = await _save_states(conn, market_id, T1, entry=True)

    assert await _apply(conn, market_id, _candle(T1, "100.3"), states) is None
    current = await positions.get_open(conn, market_id)
    assert current is not None and current.entry_time == T0
    assert (await infos.list_by_market(conn, market_id)) == states


async def test_buy_stores_exit_settings_at_entry(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    states = await _save_states(conn, market_id, T0, entry=True)
    settings = ExitSettings(take_profit_rate=Decimal("2.5"), stop_loss_rate=Decimal("4"), min_profit_rate=Decimal("1"))

    trade = await apply_paper_decision(
        conn,
        market_id=market_id,
        candle=_candle(T0, "100"),
        states=states,
        exit_settings=settings,
        stochastic_entry_max=50.0,
        allow_buy=True,
        order_settings=NO_ADD,
    )

    assert trade is not None
    position = trade.position
    assert (position.take_profit_rate, position.stop_loss_rate, position.min_profit_rate) == (
        Decimal("2.5"),
        Decimal("4"),
        Decimal("1"),
    )


@pytest.mark.parametrize(
    ("stored_stop_loss", "current_stop_loss", "sells"),
    [
        # 매수할 때 손절 3%로 샀다면, 설정을 1%로 바꿔도 -2%에서는 팔지 않는다
        ("3", "1", False),
        # 매수할 때 손절 1%로 샀다면, 설정을 3%로 바꿔도 -2%에서 판다
        ("1", "3", True),
    ],
)
async def test_holding_uses_exit_settings_from_entry(
    conn: asyncpg.Connection, stored_stop_loss: str, current_stop_loss: str, sells: bool
) -> None:
    market_id = await _market_id(conn)
    await positions.open(
        conn,
        market_id=market_id,
        entry_time=T0,
        entry_price=Decimal("100"),
        average_price=Decimal("100"),
        exit_settings=ExitSettings(stop_loss_rate=Decimal(stored_stop_loss)),
    )
    states = await _save_states(conn, market_id, T1)

    # 순수익률 = (98 * 0.9995 - 100) / 100 * 100 = -2.049%
    trade = await apply_paper_decision(
        conn,
        market_id=market_id,
        candle=_candle(T1, "98"),
        states=states,
        exit_settings=ExitSettings(stop_loss_rate=Decimal(current_stop_loss)),
        stochastic_entry_max=50.0,
        allow_buy=True,
        order_settings=NO_ADD,
    )

    if sells:
        assert trade is not None and trade.decision.reason == Reason.STOP_LOSS
    else:
        assert trade is None
        assert await positions.get_open(conn, market_id) is not None


# ---------------------------------------------------------------------------
# 실시간 손절·익절 (매매 로직 5.1)
# ---------------------------------------------------------------------------


def _tick(price: str, traded_at: datetime = T1 + timedelta(seconds=5)) -> Ticker:
    return Ticker(market="KRW-BTC", price=Decimal(price), traded_at=traded_at)


async def _hold(conn: asyncpg.Connection, market_id: int, exit_settings: ExitSettings | None = None) -> None:
    await positions.open(
        conn,
        market_id=market_id,
        entry_time=T0,
        entry_price=Decimal("100"),
        average_price=Decimal("100"),
        exit_settings=exit_settings or ExitSettings(),
    )


async def test_realtime_without_position_does_nothing(conn: asyncpg.Connection) -> None:
    assert await apply_realtime_exit(conn, market_id=await _market_id(conn), ticker=_tick("50")) is None


@pytest.mark.parametrize("price", ["97.1", "100", "101.5"])
async def test_realtime_within_range_keeps_position(conn: asyncpg.Connection, price: str) -> None:
    market_id = await _market_id(conn)
    await _hold(conn, market_id)

    # 수수료를 빼면 97.1 → -2.95%, 101.5 → 1.449%로 손절·익절 기준에 닿지 않는다
    assert await apply_realtime_exit(conn, market_id=market_id, ticker=_tick(price)) is None
    assert await positions.get_open(conn, market_id) is not None


@pytest.mark.parametrize(("price", "reason"), [("97", Reason.STOP_LOSS), ("101.6", Reason.TAKE_PROFIT)])
async def test_realtime_stop_loss_and_take_profit(conn: asyncpg.Connection, price: str, reason: Reason) -> None:
    market_id = await _market_id(conn)
    await _hold(conn, market_id)
    states = await _save_states(conn, market_id, T0, overbought=True)
    traded_at = T1 + timedelta(minutes=7, seconds=3)

    trade = await apply_realtime_exit(conn, market_id=market_id, ticker=_tick(price, traded_at))

    assert trade is not None
    assert (trade.decision.action, trade.decision.reason) == (Action.SELL, reason)
    # 판단에 쓴 체결가와 체결 시각으로 기록한다
    position = trade.position
    assert (position.status, position.exit_time, position.exit_price) == (
        PositionStatus.CLOSED,
        traded_at,
        Decimal(price),
    )
    expected = (Decimal(price) * (1 - PAPER_FEE_RATE) - Decimal("100")) / Decimal("100") * 100
    assert position.net_profit_rate == expected
    # 매도하면 신호 상태를 비운다 (매매 로직 7)
    assert _all_cleared(await infos.list_by_market(conn, market_id))
    assert len(states) == 5


async def test_realtime_never_sells_on_signals(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    await _hold(conn, market_id)
    # 과매수 매도 조건이 저장돼 있고 순수익률도 최소 기준(0.5%)을 넘지만, 실시간에는 손절·익절만 본다
    await _save_states(conn, market_id, T1, overbought=True)

    assert await apply_realtime_exit(conn, market_id=market_id, ticker=_tick("101")) is None


async def test_realtime_uses_exit_settings_from_entry(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    await _hold(conn, market_id, ExitSettings(stop_loss_rate=Decimal("1")))

    # 저장된 손절 기준 1%로 판단한다 (매매 로직 2.3)
    trade = await apply_realtime_exit(conn, market_id=market_id, ticker=_tick("98.5"))

    assert trade is not None and trade.decision.reason == Reason.STOP_LOSS


@pytest.mark.parametrize("traded_at", [T0, T0 - timedelta(seconds=1)])
async def test_realtime_ignores_ticker_before_entry(conn: asyncpg.Connection, traded_at: datetime) -> None:
    market_id = await _market_id(conn)
    await _hold(conn, market_id)

    assert await apply_realtime_exit(conn, market_id=market_id, ticker=_tick("50", traded_at)) is None
    assert await positions.get_open(conn, market_id) is not None


# 추가 매수 (매매 로직 5.6): 첫 매수 금액의 1.5배, 최대 2번
ADD_TWO = OrderSettings(add_buy_multiple=Decimal("1.5"), max_add_buys=2)


def test_paper_average_after_add_is_cost_weighted_with_fee() -> None:
    # 100원에 1만큼 샀고(평단가 100.05), 90원에 1.5만큼 더 산다
    average = paper_average_after_add(Decimal("100.05"), Decimal("1"), Decimal("1.5"), Decimal("90"))

    cost = Decimal("2.5") * Decimal("1.0005")
    volume = Decimal("1.0005") / Decimal("100.05") + Decimal("1.5") / Decimal("90")
    assert average == cost / volume
    # 금액 기준으로 샀으므로 수량 기준 평균 2.5 / (1/100 + 1.5/90) = 93.75보다 수수료만큼 높다
    assert Decimal("93.75") < average < Decimal("93.8")


async def test_add_buy_when_buy_signal_repeats_while_holding(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    await _apply(conn, market_id, _candle(T0, "100"), await _save_states(conn, market_id, T0, entry=True))
    t2 = T1 + timedelta(minutes=30)

    trade = await _apply(
        conn, market_id, _candle(t2, "98"), await _save_states(conn, market_id, t2, entry=True), order_settings=ADD_TWO
    )

    assert trade is not None and trade.decision == Decision(action=Action.BUY, reason=Reason.ADD)
    position = trade.position
    assert (position.buy_count, position.units, position.entry_price, position.status) == (
        2,
        Decimal("2.5"),
        Decimal("100"),
        PositionStatus.OPEN,
    )
    assert position.average_price == paper_average_after_add(
        Decimal("100.05"), Decimal("1"), Decimal("1.5"), Decimal("98")
    )
    # 추가 매수도 체결된 매수라 신호 상태를 비운다 (매매 로직 7)
    assert _all_cleared(await infos.list_by_market(conn, market_id))


async def test_add_buy_stops_at_limit(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    times = [T0 + timedelta(minutes=30 * i) for i in range(4)]
    reasons = []
    for i, time in enumerate(times):
        trade = await _apply(
            conn,
            market_id,
            _candle(time, str(100 - i)),
            await _save_states(conn, market_id, time, entry=True),
            order_settings=ADD_TWO,
        )
        reasons.append(None if trade is None else trade.decision.reason)

    # 첫 매수 1번 + 추가 매수 2번까지만 한다
    assert reasons == [Reason.ENTRY, Reason.ADD, Reason.ADD, None]
    position = await PaperPositionRepository().get_open(conn, market_id)
    assert position is not None and (position.buy_count, position.units) == (3, Decimal("4.0"))


async def test_add_buys_grow_from_previous_buy(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    settings = replace(ADD_TWO, add_buy_base=AddBuyBase.PREVIOUS)
    for i in range(3):
        time = T0 + timedelta(minutes=30 * i)
        await _apply(
            conn,
            market_id,
            _candle(time, str(100 - i)),
            await _save_states(conn, market_id, time, entry=True),
            order_settings=settings,
        )

    # 매수 금액은 첫 매수의 1배, 1.5배, 2.25배 (직전 매수의 1.5배씩)
    position = await PaperPositionRepository().get_open(conn, market_id)
    assert position is not None and (position.buy_count, position.units) == (3, Decimal("4.75"))


async def test_no_add_buy_when_disabled_or_window_short(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    await _apply(conn, market_id, _candle(T0, "100"), await _save_states(conn, market_id, T0, entry=True))
    t2 = T1 + timedelta(minutes=30)
    states = await _save_states(conn, market_id, t2, entry=True)

    assert await _apply(conn, market_id, _candle(t2, "98"), states) is None
    # 계산 구간이 짧은 캔들에서는 추가 매수도 하지 않는다 (매매 로직 3.0)
    assert await _apply(conn, market_id, _candle(t2, "98"), states, allow_buy=False, order_settings=ADD_TWO) is None
    position = await PaperPositionRepository().get_open(conn, market_id)
    assert position is not None and position.buy_count == 1


async def test_sell_comes_before_add_buy(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    await _apply(conn, market_id, _candle(T0, "100"), await _save_states(conn, market_id, T0, entry=True))
    t2 = T1 + timedelta(minutes=30)

    # 손절 기준(-3%) 아래에서 매수 신호가 나도 먼저 손절한다
    trade = await _apply(
        conn, market_id, _candle(t2, "96"), await _save_states(conn, market_id, t2, entry=True), order_settings=ADD_TWO
    )

    assert trade is not None and trade.decision.reason is Reason.STOP_LOSS


RECOVER = OrderSettings(max_add_buys=2, stop_loss_after_adds=True, add_buy_trigger=AddBuyTrigger.LOSS_RELAXED)


@pytest.mark.parametrize(("buys", "closed"), [(1, False), (2, False), (3, True)])
async def test_realtime_stop_loss_waits_until_adds_used_up(conn: asyncpg.Connection, buys: int, closed: bool) -> None:
    # 추가 매수가 남아 있으면 손절 기준(-3%) 아래여도 팔지 않고 복구를 기다린다
    market_id = await _market_id(conn)
    await _hold(conn, market_id)
    await conn.execute("UPDATE paper_position SET buy_count = $2 WHERE market_id = $1", market_id, buys)

    trade = await apply_realtime_exit(conn, market_id=market_id, ticker=_tick("95"), order_settings=RECOVER)

    if closed:
        assert trade is not None and trade.decision.reason is Reason.STOP_LOSS
    else:
        assert trade is None
        assert await positions.get_open(conn, market_id) is not None


async def test_realtime_stop_loss_without_recover_setting(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    await _hold(conn, market_id)

    trade = await apply_realtime_exit(conn, market_id=market_id, ticker=_tick("95"), order_settings=ADD_TWO)

    assert trade is not None and trade.decision.reason is Reason.STOP_LOSS


async def _save_relaxed(
    conn: asyncpg.Connection, market_id: int, candle_time: datetime
) -> dict[IndicatorType, IndicatorInfo]:
    """완화된 6개 조건만 참인 상태 (RSI 과매도 없음, %K·%D 60)."""
    states = {}
    for indicator_type in IndicatorType:
        info = IndicatorInfo(
            market_id=market_id,
            indicator_type=indicator_type,
            value=60.0,
            signal=55.0,
            candle_time=candle_time,
            cross_state=CrossState.GOLDEN_CROSS,
            cross_time=candle_time,
        )
        if indicator_type is IndicatorType.STOCHASTIC:
            info = replace(info, over_state=OverState.OVER_SOLD, over_time=candle_time)
        assert await infos.save(conn, info) is not None
        states[indicator_type] = info
    return states


@pytest.mark.parametrize(("close", "added"), [("95", True), ("101", False)])
async def test_loss_relaxed_add_buy_on_candle(conn: asyncpg.Connection, close: str, added: bool) -> None:
    # 손실 중(95)이면 손절 기준 아래여도 팔지 않고 완화된 조건으로 추가 매수한다. 수익 중(101)이면 사지 않는다
    market_id = await _market_id(conn)
    await _apply(conn, market_id, _candle(T0, "100"), await _save_states(conn, market_id, T0, entry=True))
    t2 = T1 + timedelta(minutes=30)

    trade = await _apply(
        conn, market_id, _candle(t2, close), await _save_relaxed(conn, market_id, t2), order_settings=RECOVER
    )

    if added:
        assert trade is not None and trade.decision == Decision(action=Action.BUY, reason=Reason.ADD)
        assert trade.position.buy_count == 2
    else:
        assert trade is None


MACD_STOP_SETTINGS = replace(RECOVER, stop_loss_by=StopLossBy.MACD_DEAD_CROSS)


async def test_realtime_has_no_fixed_stop_with_macd_stop(conn: asyncpg.Connection) -> None:
    # 추가 매수를 다 썼어도 실시간으로는 고정 % 손절을 하지 않는다 (MACD는 캔들 마감 때 판단)
    market_id = await _market_id(conn)
    await _hold(conn, market_id)
    await conn.execute("UPDATE paper_position SET buy_count = 3 WHERE market_id = $1", market_id)

    assert (
        await apply_realtime_exit(conn, market_id=market_id, ticker=_tick("80"), order_settings=MACD_STOP_SETTINGS)
        is None
    )
    assert await positions.get_open(conn, market_id) is not None


async def _save_macd_dead(
    conn: asyncpg.Connection, market_id: int, candle_time: datetime
) -> dict[IndicatorType, IndicatorInfo]:
    """MACD 3종이 모두 데드크로스인 상태 (RSI·스토캐스틱은 신호 없음)."""
    states = {}
    for indicator_type in IndicatorType:
        info = IndicatorInfo(
            market_id=market_id, indicator_type=indicator_type, value=40.0, signal=30.0, candle_time=candle_time
        )
        if indicator_type not in (IndicatorType.RSI, IndicatorType.STOCHASTIC):
            info = replace(info, cross_state=CrossState.DEAD_CROSS, cross_time=candle_time)
        assert await infos.save(conn, info) is not None
        states[indicator_type] = info
    return states


@pytest.mark.parametrize(("buys", "sold"), [(1, False), (2, False), (3, True)])
async def test_macd_stop_on_candle_after_adds_used_up(conn: asyncpg.Connection, buys: int, sold: bool) -> None:
    # 고정 % 손절 기준(-3%)에 닿지 않는 -1%에서도, 추가 매수를 다 썼고 MACD 3종이 모두 데드크로스면 판다
    market_id = await _market_id(conn)
    await _apply(conn, market_id, _candle(T0, "100"), await _save_states(conn, market_id, T0, entry=True))
    await conn.execute("UPDATE paper_position SET buy_count = $2 WHERE market_id = $1", market_id, buys)
    t2 = T1 + timedelta(minutes=30)

    trade = await _apply(
        conn,
        market_id,
        _candle(t2, "99"),
        await _save_macd_dead(conn, market_id, t2),
        order_settings=MACD_STOP_SETTINGS,
    )

    if sold:
        assert trade is not None and trade.decision == Decision(action=Action.SELL, reason=Reason.STOP_LOSS)
        assert trade.position.status is PositionStatus.CLOSED
    else:
        assert trade is None
