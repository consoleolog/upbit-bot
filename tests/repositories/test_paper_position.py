import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest

from src.app.config import ExitSettings
from src.repositories.errors import NotFoundError
from src.repositories.market import MarketRepository
from src.repositories.models import PositionStatus
from src.repositories.paper_position import PaperPositionRepository
from src.trading.reasons import Reason

pytestmark = pytest.mark.integration

repo = PaperPositionRepository()
T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = T0 + timedelta(minutes=30)


async def _market_id(conn: asyncpg.Connection) -> int:
    market = await MarketRepository().add(conn, "KRW-T" + uuid.uuid4().hex[:8].upper())
    assert market is not None
    return market.market_id


async def _open(conn: asyncpg.Connection, market_id: int) -> int:
    position = await repo.open(
        conn,
        market_id=market_id,
        entry_time=T0,
        entry_price=Decimal("100"),
        average_price=Decimal("100.05"),
        exit_settings=ExitSettings(),
    )
    assert position is not None
    return position.position_id


async def test_open_and_get_open(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)

    position = await repo.open(
        conn,
        market_id=market_id,
        entry_time=T0,
        entry_price=Decimal("100"),
        average_price=Decimal("100.05"),
        exit_settings=ExitSettings(),
    )

    assert position is not None
    assert (position.status, position.entry_time, position.entry_price, position.average_price) == (
        PositionStatus.OPEN,
        T0,
        Decimal("100"),
        Decimal("100.05"),
    )
    assert (position.exit_time, position.exit_price, position.exit_reason, position.net_profit_rate) == (
        None,
        None,
        None,
        None,
    )
    # 매수할 때의 매도 기준을 함께 남긴다 (매매 로직 2.3)
    exit_settings = ExitSettings()
    assert (position.take_profit_rate, position.stop_loss_rate, position.min_profit_rate) == (
        exit_settings.take_profit_rate,
        exit_settings.stop_loss_rate,
        exit_settings.min_profit_rate,
    )
    assert await repo.get_open(conn, market_id) == position


async def test_get_open_without_position(conn: asyncpg.Connection) -> None:
    assert await repo.get_open(conn, await _market_id(conn)) is None


async def test_second_open_in_same_market_returns_none(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    first = await _open(conn, market_id)

    # 마켓당 열린 포지션은 하나. 같은 마켓을 두 번 사지 않는다
    assert (
        await repo.open(
            conn,
            market_id=market_id,
            entry_time=T1,
            entry_price=Decimal("90"),
            average_price=Decimal("90.045"),
            exit_settings=ExitSettings(),
        )
        is None
    )
    current = await repo.get_open(conn, market_id)
    assert current is not None and current.position_id == first


async def test_close(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    position_id = await _open(conn, market_id)

    closed = await repo.close(
        conn,
        position_id=position_id,
        exit_time=T1,
        exit_price=Decimal("102"),
        exit_reason=Reason.TAKE_PROFIT,
        net_profit_rate=Decimal("1.8"),
    )

    assert (closed.status, closed.exit_time, closed.exit_price, closed.exit_reason, closed.net_profit_rate) == (
        PositionStatus.CLOSED,
        T1,
        Decimal("102"),
        Reason.TAKE_PROFIT,
        Decimal("1.8"),
    )
    assert await repo.get_open(conn, market_id) is None
    # 닫은 뒤에는 다시 살 수 있다
    assert (
        await repo.open(
            conn,
            market_id=market_id,
            entry_time=T1,
            entry_price=Decimal("101"),
            average_price=Decimal("101.0505"),
            exit_settings=ExitSettings(),
        )
        is not None
    )


async def test_close_twice_fails(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    position_id = await _open(conn, market_id)
    values = {"exit_time": T1, "exit_price": Decimal("97"), "exit_reason": Reason.STOP_LOSS}
    await repo.close(conn, position_id=position_id, net_profit_rate=Decimal("-3"), **values)

    with pytest.raises(NotFoundError, match=str(position_id)):
        await repo.close(conn, position_id=position_id, net_profit_rate=Decimal("-3"), **values)


async def test_close_in_same_candle_rejected(conn: asyncpg.Connection) -> None:
    position_id = await _open(conn, await _market_id(conn))

    with pytest.raises(asyncpg.CheckViolationError):
        await repo.close(
            conn,
            position_id=position_id,
            exit_time=T0,
            exit_price=Decimal("97"),
            exit_reason=Reason.STOP_LOSS,
            net_profit_rate=Decimal("-3"),
        )


@pytest.mark.parametrize(("entry", "average"), [("0", "0"), ("100", "99.9")])
async def test_invalid_entry_prices_rejected(conn: asyncpg.Connection, entry: str, average: str) -> None:
    market_id = await _market_id(conn)

    with pytest.raises(asyncpg.CheckViolationError):
        await repo.open(
            conn,
            market_id=market_id,
            entry_time=T0,
            entry_price=Decimal(entry),
            average_price=Decimal(average),
            exit_settings=ExitSettings(),
        )
