import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from src.repositories.indicator_info import IndicatorInfoRepository
from src.repositories.market import MarketRepository
from src.repositories.models import IndicatorInfo, IndicatorType
from src.signals import CrossState, OverState

pytestmark = pytest.mark.integration

repo = IndicatorInfoRepository()
T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=30)


async def _market_id(conn: asyncpg.Connection) -> int:
    market = await MarketRepository().add(conn, "KRW-T" + uuid.uuid4().hex[:8].upper())
    assert market is not None
    return market.market_id


def _rsi(market_id: int, candle_time: datetime = T0, **states: object) -> IndicatorInfo:
    return IndicatorInfo(
        market_id=market_id,
        indicator_type=IndicatorType.RSI,
        value=35.5,
        signal=40.25,
        candle_time=candle_time,
        **states,  # type: ignore[arg-type]
    )


async def test_save_new_and_list(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    info = _rsi(market_id, over_state=OverState.OVER_SOLD, over_time=T0)

    saved = await repo.save(conn, info)

    assert saved == info
    assert await repo.list_by_market(conn, market_id) == {IndicatorType.RSI: info}


async def test_list_empty_market(conn: asyncpg.Connection) -> None:
    assert await repo.list_by_market(conn, await _market_id(conn)) == {}


async def test_save_newer_candle_replaces_values_and_states(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    await repo.save(conn, _rsi(market_id, over_state=OverState.OVER_SOLD, over_time=T0))
    newer = replace(_rsi(market_id, T1), value=None, signal=None, cross_state=CrossState.GOLDEN_CROSS, cross_time=T1)

    assert await repo.save(conn, newer) == newer
    assert (await repo.list_by_market(conn, market_id))[IndicatorType.RSI] == newer


@pytest.mark.parametrize("candle_time", [T0, T0 - timedelta(minutes=30)])
async def test_save_same_or_older_candle_is_ignored(conn: asyncpg.Connection, candle_time: datetime) -> None:
    market_id = await _market_id(conn)
    current = _rsi(market_id, T0, cross_state=CrossState.DEAD_CROSS, cross_time=T0)
    await repo.save(conn, current)

    # 같은 마감이 두 번 처리되거나 늦게 도착한 처리는 최신 상태를 덮어쓰지 않는다
    stale = replace(_rsi(market_id, candle_time), value=99.0)

    assert await repo.save(conn, stale) is None
    assert (await repo.list_by_market(conn, market_id))[IndicatorType.RSI] == current


async def test_save_keeps_types_separate(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    rsi = _rsi(market_id)
    macd = replace(rsi, indicator_type=IndicatorType.MACD_LONG, value=-0.000123, signal=None)

    await repo.save(conn, rsi)
    await repo.save(conn, macd)

    assert await repo.list_by_market(conn, market_id) == {IndicatorType.RSI: rsi, IndicatorType.MACD_LONG: macd}


async def test_save_rejects_over_state_on_macd(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    macd = replace(
        _rsi(market_id), indicator_type=IndicatorType.MACD_SHORT, over_state=OverState.OVER_BOUGHT, over_time=T0
    )

    with pytest.raises(asyncpg.CheckViolationError):
        await repo.save(conn, macd)


async def test_save_rejects_signal_after_candle(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)

    with pytest.raises(asyncpg.CheckViolationError):
        await repo.save(conn, _rsi(market_id, T0, cross_state=CrossState.GOLDEN_CROSS, cross_time=T1))


async def test_save_unknown_market_fails(conn: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await repo.save(conn, _rsi(-1))
