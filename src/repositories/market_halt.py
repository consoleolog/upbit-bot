from datetime import datetime

import asyncpg

from src.repositories.errors import NotFoundError
from src.repositories.models import MarketHalt
from src.repositories.values import check_aware


class MarketHaltRepository:
    """실거래 매매 중단. 재시작해도 유지되고, 사람이 해제할 때만 지운다."""

    async def add(
        self, conn: asyncpg.Connection, *, market_id: int, reason: str, halted_at: datetime
    ) -> MarketHalt | None:
        """마켓을 중단한다. 이미 중단돼 있으면 처음 기록을 그대로 두고 None."""
        if not reason.strip():
            raise ValueError("halt reason must not be empty")
        check_aware("halted_at", halted_at)
        row = await conn.fetchrow(
            """
            INSERT INTO market_halt (market_id, reason, halted_at) VALUES ($1, $2, $3)
            ON CONFLICT (market_id) DO NOTHING
            RETURNING *
            """,
            market_id,
            reason,
            halted_at,
        )
        return None if row is None else MarketHalt.from_record(row)

    async def list_all(self, conn: asyncpg.Connection) -> list[MarketHalt]:
        """중단된 마켓 전체. 시작할 때 읽어 메모리 사본을 만든다."""
        rows = await conn.fetch("SELECT * FROM market_halt ORDER BY halted_at, market_id")
        return [MarketHalt.from_record(row) for row in rows]

    async def remove(self, conn: asyncpg.Connection, market_id: int) -> MarketHalt:
        """중단을 해제하고 지운 기록을 돌려준다. 중단돼 있지 않으면 NotFoundError."""
        row = await conn.fetchrow("DELETE FROM market_halt WHERE market_id = $1 RETURNING *", market_id)
        if row is None:
            raise NotFoundError(f"market halt not found: market_id={market_id}")
        return MarketHalt.from_record(row)
