import asyncpg

from src.repositories.errors import NotFoundError
from src.repositories.models import Market

# 거래하는 거래소. market 테이블의 exchange 값이다
EXCHANGE = "upbit"


class MarketRepository:
    async def add(self, conn: asyncpg.Connection, symbol: str) -> Market | None:
        """마켓을 등록한다. 이미 있으면 None. symbol은 '호가 통화-기준 통화' (KRW-BTC)."""
        quote, _, base = symbol.partition("-")
        if not quote or not base or "-" in base:
            raise ValueError(f"market symbol must look like KRW-BTC: {symbol!r}")
        row = await conn.fetchrow(
            """
            INSERT INTO market (exchange, symbol, base_currency, quote_currency) VALUES ($1, $2, $3, $4)
            ON CONFLICT (exchange, symbol) DO NOTHING
            RETURNING *
            """,
            EXCHANGE,
            symbol,
            base,
            quote,
        )
        return None if row is None else Market.from_record(row)

    async def get_by_symbol(self, conn: asyncpg.Connection, symbol: str) -> Market:
        row = await conn.fetchrow("SELECT * FROM market WHERE exchange = $1 AND symbol = $2", EXCHANGE, symbol)
        if row is None:
            raise NotFoundError(f"market not found: {symbol}")
        return Market.from_record(row)
