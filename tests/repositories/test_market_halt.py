import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import asyncpg
import pytest

from src.repositories.errors import NotFoundError
from src.repositories.market import MarketRepository
from src.repositories.market_halt import MarketHaltRepository
from src.repositories.models import MarketHalt

repo = MarketHaltRepository()
T0 = datetime(2026, 11, 1, tzinfo=UTC)


async def _market_id(conn: asyncpg.Connection) -> int:
    market = await MarketRepository().add(conn, "KRW-T" + uuid.uuid4().hex[:8].upper())
    assert market is not None
    return market.market_id


@pytest.mark.parametrize("reason", ["", "   ", "\n"])
async def test_add_rejects_empty_reason(reason: str) -> None:
    conn = MagicMock()

    with pytest.raises(ValueError, match="reason"):
        await repo.add(conn, market_id=1, reason=reason, halted_at=T0)

    # 잘못된 값은 DB에 보내기 전에 막는다
    conn.fetchrow.assert_not_called()


async def test_add_rejects_naive_time() -> None:
    with pytest.raises(ValueError, match="halted_at"):
        await repo.add(MagicMock(), market_id=1, reason="rejected", halted_at=datetime(2026, 11, 1))


@pytest.mark.integration
async def test_add_list_and_remove(conn: asyncpg.Connection) -> None:
    first, second = await _market_id(conn), await _market_id(conn)

    added = await repo.add(conn, market_id=second, reason="order rejected", halted_at=T0 + timedelta(minutes=1))
    await repo.add(conn, market_id=first, reason="cannot apply order", halted_at=T0)

    assert added == MarketHalt(market_id=second, reason="order rejected", halted_at=T0 + timedelta(minutes=1))
    # 중단된 순서대로 읽는다
    halts = [halt for halt in await repo.list_all(conn) if halt.market_id in (first, second)]
    assert [halt.market_id for halt in halts] == [first, second]

    assert await repo.remove(conn, second) == added
    assert [halt.market_id for halt in await repo.list_all(conn)].count(second) == 0


@pytest.mark.integration
async def test_add_again_keeps_first_reason(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    first = await repo.add(conn, market_id=market_id, reason="first", halted_at=T0)

    assert await repo.add(conn, market_id=market_id, reason="second", halted_at=T0 + timedelta(hours=1)) is None
    # 처음 기록이 그대로이고, 트랜잭션도 계속 쓸 수 있다
    assert [halt for halt in await repo.list_all(conn) if halt.market_id == market_id] == [first]


@pytest.mark.integration
async def test_remove_not_halted_market(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)

    with pytest.raises(NotFoundError, match=str(market_id)):
        await repo.remove(conn, market_id)


@pytest.mark.integration
async def test_add_unknown_market_rejected(conn: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await repo.add(conn, market_id=2_000_000_000, reason="rejected", halted_at=T0)


@pytest.mark.integration
async def test_db_rejects_blank_reason(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)

    # Repository 검사를 거치지 않고 넣어도 DB가 막는다
    with pytest.raises(asyncpg.CheckViolationError, match="market_halt_reason_check"):
        await conn.execute(
            "INSERT INTO market_halt (market_id, reason, halted_at) VALUES ($1, $2, $3)", market_id, " ", T0
        )
