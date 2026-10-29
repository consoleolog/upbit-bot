from datetime import datetime
from decimal import Decimal

import asyncpg

from src.app.config import ExitSettings
from src.repositories.errors import NotFoundError
from src.repositories.models import PaperPosition
from src.trading.reasons import Reason


class PaperPositionRepository:
    async def get_open(self, conn: asyncpg.Connection, market_id: int) -> PaperPosition | None:
        """마켓의 열린 모의 포지션. 없으면 None."""
        row = await conn.fetchrow("SELECT * FROM paper_position WHERE market_id = $1 AND status = 'OPEN'", market_id)
        return None if row is None else PaperPosition.from_record(row)

    async def open(
        self,
        conn: asyncpg.Connection,
        *,
        market_id: int,
        entry_time: datetime,
        entry_price: Decimal,
        average_price: Decimal,
        exit_settings: ExitSettings,
    ) -> PaperPosition | None:
        """모의 매수를 기록한다. 매도 기준도 함께 남긴다. 마켓에 이미 열린 포지션이 있으면 기록하지 않고 None."""
        row = await conn.fetchrow(
            """
            INSERT INTO paper_position (
                market_id, entry_time, entry_price, average_price, take_profit_rate, stop_loss_rate, min_profit_rate
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (market_id) WHERE status = 'OPEN' DO NOTHING
            RETURNING *
            """,
            market_id,
            entry_time,
            entry_price,
            average_price,
            exit_settings.take_profit_rate,
            exit_settings.stop_loss_rate,
            exit_settings.min_profit_rate,
        )
        return None if row is None else PaperPosition.from_record(row)

    async def close(
        self,
        conn: asyncpg.Connection,
        *,
        position_id: int,
        exit_time: datetime,
        exit_price: Decimal,
        exit_reason: Reason,
        net_profit_rate: Decimal,
    ) -> PaperPosition:
        """열린 모의 포지션을 매도로 닫는다. 열린 포지션이 아니면 NotFoundError."""
        row = await conn.fetchrow(
            """
            UPDATE paper_position
            SET status = 'CLOSED', exit_time = $2, exit_price = $3, exit_reason = $4, net_profit_rate = $5
            WHERE position_id = $1 AND status = 'OPEN'
            RETURNING *
            """,
            position_id,
            exit_time,
            exit_price,
            exit_reason,
            net_profit_rate,
        )
        if row is None:
            raise NotFoundError(f"open paper position not found: {position_id}")
        return PaperPosition.from_record(row)
