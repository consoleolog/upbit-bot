import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest

from src.app.config import ExitSettings
from src.exchange.candle import Candle
from src.repositories.indicator_info import IndicatorInfoRepository
from src.repositories.market import MarketRepository
from src.repositories.models import IndicatorInfo, IndicatorType, PositionStatus
from src.repositories.paper_position import PaperPositionRepository
from src.trading.decision import Action, Reason
from src.trading.paper import PAPER_FEE_RATE, PaperTrade, apply_paper_decision
from src.trading.signals import CrossState, OverState

pytestmark = pytest.mark.integration

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = T0 + timedelta(minutes=30)
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
) -> PaperTrade | None:
    return await apply_paper_decision(
        conn,
        market_id=market_id,
        candle=candle,
        states=states,
        exit_settings=ExitSettings(),
        stochastic_entry_max=50.0,
        allow_buy=allow_buy,
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
    )

    if sells:
        assert trade is not None and trade.decision.reason == Reason.STOP_LOSS
    else:
        assert trade is None
        assert await positions.get_open(conn, market_id) is not None
