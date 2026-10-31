import uuid
from dataclasses import replace
from decimal import Decimal

import asyncpg
import pytest

from src.exchange.order import OrderSide
from src.repositories.errors import NotFoundError
from src.repositories.market import MarketRepository
from src.repositories.models import LiveOrder, LiveOrderState
from src.repositories.order import OrderRepository
from src.trading.reasons import Reason

pytestmark = pytest.mark.integration

repo = OrderRepository()


async def _market_id(conn: asyncpg.Connection) -> int:
    market = await MarketRepository().add(conn, "KRW-T" + uuid.uuid4().hex[:8].upper())
    assert market is not None
    return market.market_id


async def _buy(conn: asyncpg.Connection, market_id: int) -> LiveOrder:
    order = await repo.create_buy(conn, market_id=market_id, amount=Decimal("5000"))
    assert order is not None
    return order


async def _finish(conn: asyncpg.Connection, order: LiveOrder, state: LiveOrderState) -> LiveOrder:
    executed = state in (LiveOrderState.DONE, LiveOrderState.CANCEL)
    return await repo.record_result(
        conn,
        order_id=order.order_id,
        state=state,
        executed_volume=Decimal("0.001") if executed else Decimal(0),
        executed_funds=Decimal("4997.5") if executed else Decimal(0),
        paid_fee=Decimal("2.49875") if executed else Decimal(0),
    )


async def test_create_buy(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)

    order = await _buy(conn, market_id)

    assert (order.market_id, order.side, order.reason, order.state) == (
        market_id,
        OrderSide.BID,
        Reason.ENTRY,
        LiveOrderState.PENDING,
    )
    assert (order.requested_amount, order.requested_volume) == (Decimal("5000"), None)
    assert (order.executed_volume, order.executed_funds, order.paid_fee) == (0, 0, 0)
    # 업비트에 보낼 identifier. 업비트 제한(64자) 안이다
    assert str(uuid.UUID(order.identifier)) == order.identifier
    assert len(order.identifier) <= 64


async def test_create_sell(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)

    order = await repo.create_sell(conn, market_id=market_id, volume=Decimal("0.001"), reason=Reason.TAKE_PROFIT)

    assert order is not None
    assert (order.side, order.reason, order.requested_amount, order.requested_volume) == (
        OrderSide.ASK,
        Reason.TAKE_PROFIT,
        None,
        Decimal("0.001"),
    )


async def test_each_order_has_new_identifier(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    first = await _buy(conn, market_id)
    await _finish(conn, first, LiveOrderState.FAILED)

    second = await _buy(conn, market_id)

    # 업비트 identifier는 실패한 주문의 것도 다시 쓸 수 없다
    assert second.identifier != first.identifier


@pytest.mark.parametrize("state", [LiveOrderState.PENDING, LiveOrderState.WAIT])
async def test_no_second_order_while_unfinished(conn: asyncpg.Connection, state: LiveOrderState) -> None:
    market_id = await _market_id(conn)
    order = await _buy(conn, market_id)
    if state is LiveOrderState.WAIT:
        order = await _finish(conn, order, LiveOrderState.WAIT)

    # 매매 로직 5.1: 마켓당 주문은 한 번에 최대 1건. 예외 없이 None으로 알려 트랜잭션을 계속 쓸 수 있다
    assert await repo.create_buy(conn, market_id=market_id, amount=Decimal("5000")) is None
    assert await repo.create_sell(conn, market_id=market_id, volume=Decimal("1"), reason=Reason.STOP_LOSS) is None
    assert await repo.get_unfinished(conn, market_id) == order
    # 다른 마켓에는 주문할 수 있다
    await _buy(conn, await _market_id(conn))


@pytest.mark.parametrize("state", [LiveOrderState.DONE, LiveOrderState.CANCEL, LiveOrderState.FAILED])
async def test_finished_order_allows_next_order(conn: asyncpg.Connection, state: LiveOrderState) -> None:
    market_id = await _market_id(conn)
    await _finish(conn, await _buy(conn, market_id), state)

    assert await repo.get_unfinished(conn, market_id) is None
    assert await repo.create_sell(conn, market_id=market_id, volume=Decimal("1"), reason=Reason.STOP_LOSS) is not None


async def test_get_unfinished(conn: asyncpg.Connection) -> None:
    market_id = await _market_id(conn)
    assert await repo.get_unfinished(conn, market_id) is None

    order = await _buy(conn, market_id)
    assert await repo.get_unfinished(conn, market_id) == order

    waiting = await _finish(conn, order, LiveOrderState.WAIT)
    assert await repo.get_unfinished(conn, market_id) == waiting


async def test_record_result(conn: asyncpg.Connection) -> None:
    order = await _buy(conn, await _market_id(conn))

    done = await _finish(conn, order, LiveOrderState.DONE)

    assert done == replace(
        order,
        state=LiveOrderState.DONE,
        executed_volume=Decimal("0.001"),
        executed_funds=Decimal("4997.5"),
        paid_fee=Decimal("2.49875"),
    )
    assert await repo.get_unfinished(conn, order.market_id) is None


@pytest.mark.parametrize("state", [LiveOrderState.DONE, LiveOrderState.CANCEL, LiveOrderState.FAILED])
async def test_finished_order_result_not_recorded_twice(conn: asyncpg.Connection, state: LiveOrderState) -> None:
    order = await _finish(conn, await _buy(conn, await _market_id(conn)), state)

    # 같은 체결을 포지션에 두 번 반영하지 않도록, 끝난 주문에는 다시 기록하지 않는다
    with pytest.raises(NotFoundError, match=str(order.order_id)):
        await _finish(conn, order, LiveOrderState.DONE)


async def test_record_result_of_unknown_order_fails(conn: asyncpg.Connection) -> None:
    order = await _buy(conn, await _market_id(conn))

    with pytest.raises(NotFoundError):
        await _finish(conn, replace(order, order_id=-1), LiveOrderState.DONE)


async def test_result_cannot_be_pending(conn: asyncpg.Connection) -> None:
    order = await _buy(conn, await _market_id(conn))

    with pytest.raises(ValueError, match="pending"):
        await _finish(conn, order, LiveOrderState.PENDING)


@pytest.mark.parametrize(
    ("state", "volume", "funds", "fee"),
    [
        # 거래소가 접수하지 않은 주문에는 체결이 없다
        (LiveOrderState.FAILED, "0.001", "4997.5", "0"),
        # 주문 금액보다 많이 체결될 수 없다
        (LiveOrderState.DONE, "0.001", "5000.1", "2.5"),
    ],
)
async def test_invalid_result_rejected(
    conn: asyncpg.Connection, state: LiveOrderState, volume: str, funds: str, fee: str
) -> None:
    order = await _buy(conn, await _market_id(conn))

    with pytest.raises(asyncpg.CheckViolationError):
        await repo.record_result(
            conn,
            order_id=order.order_id,
            state=state,
            executed_volume=Decimal(volume),
            executed_funds=Decimal(funds),
            paid_fee=Decimal(fee),
        )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "0", "-1"])
async def test_unusable_requested_value_not_recorded(conn: asyncpg.Connection, value: str) -> None:
    market_id = await _market_id(conn)

    # NaN과 Infinity는 DB의 "> 0" 제약을 통과하므로 기록하기 전에 막는다. 기록되면 보낼 수 없는 주문이 마켓을 막는다
    with pytest.raises(ValueError, match="amount"):
        await repo.create_buy(conn, market_id=market_id, amount=Decimal(value))
    with pytest.raises(ValueError, match="volume"):
        await repo.create_sell(conn, market_id=market_id, volume=Decimal(value), reason=Reason.STOP_LOSS)

    assert await repo.get_unfinished(conn, market_id) is None


@pytest.mark.parametrize("field", ["executed_volume", "executed_funds", "paid_fee"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1"])
async def test_unusable_result_not_recorded(conn: asyncpg.Connection, field: str, value: str) -> None:
    order = await _buy(conn, await _market_id(conn))
    result = {"executed_volume": Decimal("0.001"), "executed_funds": Decimal("4997.5"), "paid_fee": Decimal("2.5")}

    with pytest.raises(ValueError, match=field):
        await repo.record_result(
            conn, order_id=order.order_id, state=LiveOrderState.DONE, **{**result, field: Decimal(value)}
        )

    assert await repo.get_unfinished(conn, order.market_id) == order
