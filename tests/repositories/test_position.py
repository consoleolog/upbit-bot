import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest

from src.app.config import ExitSettings
from src.repositories.errors import NotFoundError
from src.repositories.market import MarketRepository
from src.repositories.models import LivePosition, PositionStatus
from src.repositories.position import PositionRepository
from src.trading.reasons import Reason

pytestmark = pytest.mark.integration

repo = PositionRepository()
T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = T0 + timedelta(minutes=5)
EXIT = ExitSettings(take_profit_rate=Decimal("1.5"), stop_loss_rate=Decimal("3"), min_profit_rate=Decimal("0.5"))


async def _market_id(conn: asyncpg.Connection) -> int:
    market = await MarketRepository().add(conn, "KRW-T" + uuid.uuid4().hex[:8].upper())
    assert market is not None
    return market.market_id


async def _open(conn: asyncpg.Connection, market_id: int) -> LivePosition:
    # 0.001개를 4997.5원에 사고 수수료 2.49875원을 냈다
    position = await repo.open(
        conn,
        market_id=market_id,
        volume=Decimal("0.001"),
        cost=Decimal("4999.99875"),
        opened_at=T0,
        exit_settings=EXIT,
        first_buy_amount=Decimal("5000"),
    )
    assert position is not None
    return position


async def test_open_and_get_open(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    assert await repo.get_open(conn, market_id) is None

    position = await _open(conn, market_id)

    assert position == LivePosition(
        position_id=position.position_id,
        market_id=market_id,
        status=PositionStatus.OPEN,
        take_profit_rate=Decimal("1.5"),
        stop_loss_rate=Decimal("3"),
        min_profit_rate=Decimal("0.5"),
        entry_volume=Decimal("0.001"),
        entry_cost=Decimal("4999.99875"),
        sold_volume=Decimal(0),
        sold_proceeds=Decimal(0),
        opened_at=T0,
        closed_at=None,
        exit_reason=None,
        buy_count=1,
        first_buy_amount=Decimal("5000"),
    )
    assert await repo.get_open(conn, market_id) == position
    # 평단가는 매수 수수료를 포함한다 (매매 로직 5.2)
    assert position.average_price == Decimal("4999998.75")
    assert position.holding_volume == Decimal("0.001")


async def test_second_open_position_in_market_not_recorded(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    position = await _open(conn, market_id)

    # 예외 없이 None으로 알려 트랜잭션을 계속 쓸 수 있다
    assert (
        await repo.open(
            conn,
            market_id=market_id,
            volume=Decimal("1"),
            cost=Decimal("1"),
            opened_at=T1,
            exit_settings=EXIT,
            first_buy_amount=Decimal("5000"),
        )
        is None
    )
    assert await repo.get_open(conn, market_id) == position


async def test_partial_and_full_sell(conn: asyncpg.Connection) -> None:
    position = await _open(conn, await _market_id(conn))

    # 시장가 매도가 일부만 체결되면 남은 수량을 보유한다
    partial = await repo.add_sell(
        conn, position_id=position.position_id, volume=Decimal("0.0004"), proceeds=Decimal("2000")
    )
    assert (partial.status, partial.holding_volume, partial.sold_proceeds) == (
        PositionStatus.OPEN,
        Decimal("0.0006"),
        Decimal("2000"),
    )

    full = await repo.add_sell(
        conn, position_id=position.position_id, volume=Decimal("0.0006"), proceeds=Decimal("3010.5")
    )
    assert (full.holding_volume, full.sold_volume, full.sold_proceeds) == (
        Decimal("0"),
        Decimal("0.001"),
        Decimal("5010.5"),
    )


async def test_sell_more_than_holding_rejected(conn: asyncpg.Connection) -> None:
    position = await _open(conn, await _market_id(conn))
    await repo.add_sell(conn, position_id=position.position_id, volume=Decimal("0.0006"), proceeds=Decimal("3000"))

    with pytest.raises(asyncpg.CheckViolationError):
        await repo.add_sell(conn, position_id=position.position_id, volume=Decimal("0.0005"), proceeds=Decimal("2500"))


async def test_close(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    position = await _open(conn, market_id)
    sold = await repo.add_sell(
        conn, position_id=position.position_id, volume=Decimal("0.001"), proceeds=Decimal("4848.5")
    )

    closed = await repo.close(conn, position_id=position.position_id, closed_at=T1, exit_reason=Reason.STOP_LOSS)

    assert closed == replace(sold, status=PositionStatus.CLOSED, closed_at=T1, exit_reason=Reason.STOP_LOSS)
    assert await repo.get_open(conn, market_id) is None
    # 닫힌 뒤에는 같은 마켓에 새 포지션을 열 수 있다
    await _open(conn, market_id)


async def test_closed_position_not_changed(conn: asyncpg.Connection) -> None:
    position = await _open(conn, await _market_id(conn))
    await repo.close(conn, position_id=position.position_id, closed_at=T1, exit_reason=Reason.TAKE_PROFIT)

    with pytest.raises(NotFoundError, match=str(position.position_id)):
        await repo.add_sell(conn, position_id=position.position_id, volume=Decimal("0.001"), proceeds=Decimal("5000"))
    with pytest.raises(NotFoundError, match=str(position.position_id)):
        await repo.close(conn, position_id=position.position_id, closed_at=T1, exit_reason=Reason.TAKE_PROFIT)


async def test_close_before_open_rejected(conn: asyncpg.Connection) -> None:
    position = await _open(conn, await _market_id(conn))

    with pytest.raises(asyncpg.CheckViolationError):
        await repo.close(
            conn, position_id=position.position_id, closed_at=T0 - timedelta(seconds=1), exit_reason=Reason.STOP_LOSS
        )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "0", "-1"])
@pytest.mark.parametrize("field", ["volume", "cost"])
async def test_unusable_entry_not_recorded(conn: asyncpg.Connection, field: str, value: str) -> None:
    market_id = await _market_id(conn)
    entry = {"volume": Decimal("0.001"), "cost": Decimal("5000")}

    # NaN과 Infinity는 DB의 "> 0" 제약을 통과하므로 기록하기 전에 막는다
    with pytest.raises(ValueError, match=field):
        await repo.open(
            conn,
            market_id=market_id,
            opened_at=T0,
            exit_settings=EXIT,
            first_buy_amount=Decimal("5000"),
            **{**entry, field: Decimal(value)},
        )

    assert await repo.get_open(conn, market_id) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("volume", "NaN"),
        ("volume", "Infinity"),
        ("volume", "0"),
        ("volume", "-0.001"),
        ("proceeds", "NaN"),
        ("proceeds", "Infinity"),
        ("proceeds", "-1"),
    ],
)
async def test_unusable_sell_not_recorded(conn: asyncpg.Connection, field: str, value: str) -> None:
    position = await _open(conn, await _market_id(conn))
    sell = {"volume": Decimal("0.001"), "proceeds": Decimal("5000")}

    with pytest.raises(ValueError, match=field):
        await repo.add_sell(conn, position_id=position.position_id, **{**sell, field: Decimal(value)})

    assert await repo.get_open(conn, position.market_id) == position


async def test_naive_times_rejected(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    naive = datetime(2026, 1, 1)

    with pytest.raises(ValueError, match="opened_at"):
        await repo.open(
            conn,
            market_id=market_id,
            volume=Decimal("1"),
            cost=Decimal("1"),
            opened_at=naive,
            exit_settings=EXIT,
            first_buy_amount=Decimal("5000"),
        )
    position = await _open(conn, market_id)
    with pytest.raises(ValueError, match="closed_at"):
        await repo.close(conn, position_id=position.position_id, closed_at=naive, exit_reason=Reason.STOP_LOSS)


async def test_add_buy_accumulates_with_fee_weighted_average(conn: asyncpg.Connection) -> None:
    position = await _open(conn, await _market_id(conn))

    # 0.0015개를 7,500원에 더 사고 수수료 3.75원을 냈다
    added = await repo.add_buy(
        conn, position_id=position.position_id, volume=Decimal("0.0015"), cost=Decimal("7503.75")
    )

    assert (added.entry_volume, added.entry_cost, added.buy_count) == (Decimal("0.0025"), Decimal("12503.74875"), 2)
    # 평단가는 수수료를 포함한 수량 가중 평균이고, 첫 매수 금액과 매도 기준은 그대로다 (매매 로직 2.3, 5.6)
    assert added.average_price == Decimal("12503.74875") / Decimal("0.0025")
    assert (added.first_buy_amount, added.stop_loss_rate) == (position.first_buy_amount, position.stop_loss_rate)


async def test_add_buy_to_closed_position_fails(conn: asyncpg.Connection) -> None:
    position = await _open(conn, await _market_id(conn))
    await repo.close(conn, position_id=position.position_id, closed_at=T1, exit_reason=Reason.STOP_LOSS)

    with pytest.raises(NotFoundError, match=str(position.position_id)):
        await repo.add_buy(conn, position_id=position.position_id, volume=Decimal("1"), cost=Decimal("1"))


@pytest.mark.parametrize("field", ["volume", "cost"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "0", "-1"])
async def test_unusable_add_buy_not_recorded(conn: asyncpg.Connection, field: str, value: str) -> None:
    position = await _open(conn, await _market_id(conn))
    add = {"volume": Decimal("0.001"), "cost": Decimal("5000")}

    with pytest.raises(ValueError, match=field):
        await repo.add_buy(conn, position_id=position.position_id, **{**add, field: Decimal(value)})

    assert await repo.get_open(conn, position.market_id) == position


@pytest.mark.parametrize("value", ["NaN", "0", "-1"])
async def test_unusable_first_buy_amount_not_recorded(conn: asyncpg.Connection, value: str) -> None:
    market_id = await _market_id(conn)

    with pytest.raises(ValueError, match="first_buy_amount"):
        await repo.open(
            conn,
            market_id=market_id,
            volume=Decimal("0.001"),
            cost=Decimal("5000"),
            opened_at=T0,
            exit_settings=EXIT,
            first_buy_amount=Decimal(value),
        )
