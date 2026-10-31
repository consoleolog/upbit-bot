"""실거래 주문(orders)·포지션(position) 테이블의 제약 (실제 PostgreSQL).

Repository가 아직 없으므로 SQL로 직접 넣어, 잘못된 값과 중복을 DB가 막는지 확인한다.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from src.repositories.market import MarketRepository

pytestmark = pytest.mark.integration

T0 = datetime(2026, 1, 1, tzinfo=UTC)

BUY = {"side": "bid", "reason": "ENTRY", "requested_amount": Decimal("5000")}
SELL = {"side": "ask", "reason": "STOP_LOSS", "requested_volume": Decimal("0.001")}

POSITION = {
    "take_profit_rate": Decimal("1.5"),
    "stop_loss_rate": Decimal("3"),
    "min_profit_rate": Decimal("0.3"),
    "entry_volume": Decimal("0.001"),
    "entry_cost": Decimal("5002.5"),
    "opened_at": T0,
}


async def _market_id(conn: asyncpg.Connection) -> int:
    market = await MarketRepository().add(conn, "KRW-T" + uuid.uuid4().hex[:8].upper())
    assert market is not None
    return market.market_id


# 시험마다 바꾸는 값만 넘기고 나머지는 DB 기본값과 같은 값으로 채운다. SQL은 고정 문자열이다
ORDER_DEFAULTS = {
    "state": "pending",
    "requested_amount": None,
    "requested_volume": None,
    "executed_volume": Decimal(0),
    "executed_funds": Decimal(0),
    "paid_fee": Decimal(0),
}
INSERT_ORDER = """
    INSERT INTO orders (market_id, side, reason, state, requested_amount, requested_volume,
                        executed_volume, executed_funds, paid_fee)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
    RETURNING *
"""
POSITION_DEFAULTS = {
    "status": "OPEN",
    "sold_volume": Decimal(0),
    "sold_proceeds": Decimal(0),
    "closed_at": None,
    "exit_reason": None,
}
INSERT_POSITION = """
    INSERT INTO position (market_id, status, take_profit_rate, stop_loss_rate, min_profit_rate, entry_volume,
                          entry_cost, sold_volume, sold_proceeds, opened_at, closed_at, exit_reason)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
    RETURNING *
"""


async def _order(conn: asyncpg.Connection, market_id: int, **values: Any) -> asyncpg.Record:
    v = {**ORDER_DEFAULTS, **BUY, **values}
    assert set(v) == {"side", "reason", *ORDER_DEFAULTS}, f"unknown column: {set(v)}"
    return await conn.fetchrow(
        INSERT_ORDER,
        market_id,
        v["side"],
        v["reason"],
        v["state"],
        v["requested_amount"],
        v["requested_volume"],
        v["executed_volume"],
        v["executed_funds"],
        v["paid_fee"],
    )


async def _position(conn: asyncpg.Connection, market_id: int, **values: Any) -> asyncpg.Record:
    v = {**POSITION_DEFAULTS, **POSITION, **values}
    assert set(v) == {*POSITION, *POSITION_DEFAULTS}, f"unknown column: {set(v)}"
    return await conn.fetchrow(
        INSERT_POSITION,
        market_id,
        v["status"],
        v["take_profit_rate"],
        v["stop_loss_rate"],
        v["min_profit_rate"],
        v["entry_volume"],
        v["entry_cost"],
        v["sold_volume"],
        v["sold_proceeds"],
        v["opened_at"],
        v["closed_at"],
        v["exit_reason"],
    )


async def _minimal_order(conn: asyncpg.Connection) -> asyncpg.Record:
    # 필수 값만 넣어 나머지가 DB 기본값으로 채워지는지 본다
    return await conn.fetchrow(
        "INSERT INTO orders (market_id, side, reason, requested_amount) VALUES ($1, 'bid', 'ENTRY', 5000) RETURNING *",
        await _market_id(conn),
    )


async def test_order_defaults(conn: asyncpg.Connection) -> None:
    first = await _minimal_order(conn)
    second = await _minimal_order(conn)

    # identifier는 DB가 만들고, 업비트 identifier 길이 제한(64자) 안이다
    assert isinstance(first["identifier"], uuid.UUID)
    assert first["identifier"] != second["identifier"]
    assert len(str(first["identifier"])) <= 64
    assert (first["state"], first["executed_volume"], first["executed_funds"], first["paid_fee"]) == (
        "pending",
        0,
        0,
        0,
    )


async def test_buy_and_sell_orders_accepted(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)

    await _order(
        conn,
        market_id,
        state="done",
        executed_volume=Decimal("0.001"),
        executed_funds=Decimal("4997.5"),
        paid_fee=Decimal("2.49875"),
    )
    sell = await _order(
        conn,
        market_id,
        **{**SELL, "requested_amount": None},
        state="cancel",
        executed_volume=Decimal("0.0005"),
        executed_funds=Decimal("2500"),
        paid_fee=Decimal("1.25"),
    )

    # 시장가 주문은 일부만 체결되고 남은 양이 취소될 수 있다
    assert (sell["state"], sell["executed_volume"]) == ("cancel", Decimal("0.0005"))


@pytest.mark.parametrize("state", ["pending", "wait"])
async def test_one_unfinished_order_per_market(conn: asyncpg.Connection, state: str) -> None:
    market_id = await _market_id(conn)
    await _order(conn, market_id, state=state)

    # 매매 로직 5.1: 마켓당 주문은 한 번에 최대 1건
    with pytest.raises(asyncpg.UniqueViolationError):
        await _order(conn, market_id, **{**SELL, "requested_amount": None})


async def test_finished_orders_do_not_block_new_order(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    await _order(conn, market_id, state="done", executed_volume=Decimal("0.001"), executed_funds=Decimal("4997.5"))
    await _order(conn, market_id, state="cancel")
    await _order(conn, market_id, state="failed")

    await _order(conn, market_id)
    # 다른 마켓의 미완료 주문과는 상관없다
    await _order(conn, await _market_id(conn))


async def test_identifier_is_unique(conn: asyncpg.Connection) -> None:
    order = await _minimal_order(conn)

    with pytest.raises(asyncpg.UniqueViolationError):
        await conn.execute(
            "INSERT INTO orders (market_id, side, reason, requested_amount, identifier)"
            " VALUES ($1, 'bid', 'ENTRY', 5000, $2)",
            await _market_id(conn),
            order["identifier"],
        )


@pytest.mark.parametrize(
    "values",
    [
        {"side": "buy"},
        {"state": "watch"},
        {"state": "unknown"},
        # 매수는 신규 매수, 매도는 매도 사유만
        {"reason": "STOP_LOSS"},
        {**SELL, "requested_amount": None, "reason": "ENTRY"},
        {**SELL, "requested_amount": None, "reason": "MANUAL"},
        # 매수는 금액, 매도는 수량만 있어야 한다
        {"requested_amount": None},
        {"requested_amount": Decimal("0")},
        {"requested_volume": Decimal("0.001")},
        {**SELL, "requested_amount": None, "requested_volume": None},
        {**SELL},
        # 주문한 것보다 많이 체결될 수 없다
        {"state": "done", "executed_volume": Decimal("0.001"), "executed_funds": Decimal("5000.1")},
        {
            **SELL,
            "requested_amount": None,
            "state": "done",
            "executed_volume": Decimal("0.0011"),
            "executed_funds": Decimal("5500"),
        },
        # 체결 수량과 금액은 함께 있다
        {"state": "done", "executed_volume": Decimal("0.001")},
        {"state": "done", "executed_funds": Decimal("5000")},
        {"state": "done", "executed_volume": Decimal("-0.001"), "executed_funds": Decimal("5000")},
        {"state": "done", "paid_fee": Decimal("-1")},
        # 거래소가 접수하지 않은 주문에는 체결도 수수료도 없다
        {"state": "pending", "executed_volume": Decimal("0.001"), "executed_funds": Decimal("5000")},
        {"state": "failed", "executed_volume": Decimal("0.001"), "executed_funds": Decimal("5000")},
        {"state": "failed", "paid_fee": Decimal("1")},
    ],
)
async def test_invalid_order_rejected(conn: asyncpg.Connection, values: dict[str, Any]) -> None:
    market_id = await _market_id(conn)

    with pytest.raises(asyncpg.CheckViolationError):
        await _order(conn, market_id, **values)


async def test_position_defaults(conn: asyncpg.Connection) -> None:
    position = await conn.fetchrow(
        """
        INSERT INTO position (market_id, take_profit_rate, stop_loss_rate, min_profit_rate,
                              entry_volume, entry_cost, opened_at)
        VALUES ($1, 1.5, 3, 0.3, 0.001, 5002.5, $2)
        RETURNING *
        """,
        await _market_id(conn),
        T0,
    )

    assert (position["status"], position["sold_volume"], position["sold_proceeds"]) == ("OPEN", 0, 0)
    assert (position["closed_at"], position["exit_reason"]) == (None, None)


async def test_closed_position_accepted(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)

    await _position(
        conn,
        market_id,
        status="CLOSED",
        sold_volume=Decimal("0.001"),
        sold_proceeds=Decimal("4850"),
        closed_at=T0 + timedelta(minutes=5),
        exit_reason="STOP_LOSS",
    )
    # 닫힌 포지션은 새 포지션을 막지 않는다
    await _position(conn, market_id)


async def test_one_open_position_per_market(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    await _position(conn, market_id)

    with pytest.raises(asyncpg.UniqueViolationError):
        await _position(conn, market_id)


@pytest.mark.parametrize(
    "values",
    [
        {"status": "HOLD"},
        {"take_profit_rate": Decimal("0")},
        {"stop_loss_rate": Decimal("0")},
        {"min_profit_rate": Decimal("-0.1")},
        {"entry_volume": Decimal("0")},
        {"entry_cost": Decimal("0")},
        # 보유한 것보다 많이 팔 수 없다
        {"sold_volume": Decimal("0.0011")},
        {"sold_volume": Decimal("-0.001")},
        {"sold_proceeds": Decimal("-1")},
        # 열린 포지션에는 종료 정보가 없고, 닫힌 포지션에는 모두 있다
        {"exit_reason": "STOP_LOSS"},
        {"closed_at": T0},
        {"status": "CLOSED", "closed_at": T0},
        {"status": "CLOSED", "exit_reason": "STOP_LOSS"},
        {"status": "CLOSED", "closed_at": T0, "exit_reason": "ENTRY"},
        {"status": "CLOSED", "closed_at": T0 - timedelta(seconds=1), "exit_reason": "STOP_LOSS"},
    ],
)
async def test_invalid_position_rejected(conn: asyncpg.Connection, values: dict[str, Any]) -> None:
    market_id = await _market_id(conn)

    with pytest.raises(asyncpg.CheckViolationError):
        await _position(conn, market_id, **values)
