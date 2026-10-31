from decimal import Decimal

import asyncpg

from src.exchange.order import OrderSide
from src.repositories.errors import NotFoundError
from src.repositories.models import LiveOrder, LiveOrderState
from src.repositories.values import check_decimal
from src.trading.reasons import Reason

# 마켓당 끝나지 않은 주문은 하나다 (orders_unfinished_market 인덱스). 이미 있으면 기록하지 않는다
_INSERT = """
    INSERT INTO orders (market_id, side, reason, requested_amount, requested_volume)
    VALUES ($1, $2, $3, $4, $5)
    ON CONFLICT (market_id) WHERE state IN ('pending', 'wait') DO NOTHING
    RETURNING *
"""


class OrderRepository:
    async def create_buy(self, conn: asyncpg.Connection, *, market_id: int, amount: Decimal) -> LiveOrder | None:
        """원화 amount의 신규 매수 주문을 거래소에 보내기 전에 기록한다.

        마켓에 끝나지 않은 주문이 있으면 기록하지 않고 None. 그때는 주문을 보내지 않는다.
        """
        check_decimal("amount", amount, positive=True)
        row = await conn.fetchrow(_INSERT, market_id, OrderSide.BID, Reason.ENTRY, amount, None)
        return None if row is None else LiveOrder.from_record(row)

    async def create_sell(
        self, conn: asyncpg.Connection, *, market_id: int, volume: Decimal, reason: Reason
    ) -> LiveOrder | None:
        """수량 volume의 매도 주문을 거래소에 보내기 전에 기록한다. 끝나지 않은 주문이 있으면 create_buy와 같다."""
        check_decimal("volume", volume, positive=True)
        row = await conn.fetchrow(_INSERT, market_id, OrderSide.ASK, reason, None, volume)
        return None if row is None else LiveOrder.from_record(row)

    async def get_unfinished(self, conn: asyncpg.Connection, market_id: int) -> LiveOrder | None:
        """마켓의 끝나지 않은 주문(pending, wait). 없으면 None."""
        row = await conn.fetchrow(
            "SELECT * FROM orders WHERE market_id = $1 AND state IN ('pending', 'wait')", market_id
        )
        return None if row is None else LiveOrder.from_record(row)

    async def record_result(
        self,
        conn: asyncpg.Connection,
        *,
        order_id: int,
        state: LiveOrderState,
        executed_volume: Decimal,
        executed_funds: Decimal,
        paid_fee: Decimal,
    ) -> LiveOrder:
        """거래소에서 확인한 주문 상태와 체결 합계를 기록한다.

        끝나지 않은 주문에만 기록한다. 이미 끝난 주문이면 NotFoundError: 같은 체결을 포지션에 두 번 반영하지 않게 막는다.
        """
        if state is LiveOrderState.PENDING:
            raise ValueError("result state must be confirmed by the exchange, not pending")
        for name, value in (
            ("executed_volume", executed_volume),
            ("executed_funds", executed_funds),
            ("paid_fee", paid_fee),
        ):
            check_decimal(name, value, positive=False)
        row = await conn.fetchrow(
            """
            UPDATE orders
            SET state = $2, executed_volume = $3, executed_funds = $4, paid_fee = $5
            WHERE order_id = $1 AND state IN ('pending', 'wait')
            RETURNING *
            """,
            order_id,
            state,
            executed_volume,
            executed_funds,
            paid_fee,
        )
        if row is None:
            raise NotFoundError(f"unfinished order not found: {order_id}")
        return LiveOrder.from_record(row)
