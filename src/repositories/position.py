from datetime import datetime
from decimal import Decimal

import asyncpg

from src.app.config import ExitSettings
from src.repositories.errors import NotFoundError
from src.repositories.models import LivePosition
from src.repositories.values import check_aware, check_decimal
from src.trading.reasons import Reason


class PositionRepository:
    async def get_open(self, conn: asyncpg.Connection, market_id: int) -> LivePosition | None:
        """마켓의 열린 실거래 포지션. 없으면 None."""
        row = await conn.fetchrow("SELECT * FROM position WHERE market_id = $1 AND status = 'OPEN'", market_id)
        return None if row is None else LivePosition.from_record(row)

    async def open(
        self,
        conn: asyncpg.Connection,
        *,
        market_id: int,
        volume: Decimal,
        cost: Decimal,
        opened_at: datetime,
        exit_settings: ExitSettings,
    ) -> LivePosition | None:
        """체결된 매수로 포지션을 연다. cost는 매수 체결 금액 + 매수 수수료다. 매도 기준도 함께 남긴다.

        마켓에 이미 열린 포지션이 있으면 기록하지 않고 None.
        """
        check_decimal("volume", volume, positive=True)
        check_decimal("cost", cost, positive=True)
        check_aware("opened_at", opened_at)
        row = await conn.fetchrow(
            """
            INSERT INTO position (
                market_id, take_profit_rate, stop_loss_rate, min_profit_rate, entry_volume, entry_cost, opened_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (market_id) WHERE status = 'OPEN' DO NOTHING
            RETURNING *
            """,
            market_id,
            exit_settings.take_profit_rate,
            exit_settings.stop_loss_rate,
            exit_settings.min_profit_rate,
            volume,
            cost,
            opened_at,
        )
        return None if row is None else LivePosition.from_record(row)

    async def add_sell(
        self, conn: asyncpg.Connection, *, position_id: int, volume: Decimal, proceeds: Decimal
    ) -> LivePosition:
        """체결된 매도를 더한다. proceeds는 매도 체결 금액 - 매도 수수료다.

        열린 포지션이 아니면 NotFoundError. 보유 수량보다 많이 팔면 DB 제약 위반이다.
        """
        check_decimal("volume", volume, positive=True)
        check_decimal("proceeds", proceeds, positive=False)
        row = await conn.fetchrow(
            """
            UPDATE position
            SET sold_volume = sold_volume + $2, sold_proceeds = sold_proceeds + $3
            WHERE position_id = $1 AND status = 'OPEN'
            RETURNING *
            """,
            position_id,
            volume,
            proceeds,
        )
        if row is None:
            raise NotFoundError(f"open position not found: {position_id}")
        return LivePosition.from_record(row)

    async def close(
        self, conn: asyncpg.Connection, *, position_id: int, closed_at: datetime, exit_reason: Reason
    ) -> LivePosition:
        """포지션을 닫는다. 열린 포지션이 아니면 NotFoundError."""
        check_aware("closed_at", closed_at)
        row = await conn.fetchrow(
            """
            UPDATE position
            SET status = 'CLOSED', closed_at = $2, exit_reason = $3
            WHERE position_id = $1 AND status = 'OPEN'
            RETURNING *
            """,
            position_id,
            closed_at,
            exit_reason,
        )
        if row is None:
            raise NotFoundError(f"open position not found: {position_id}")
        return LivePosition.from_record(row)
