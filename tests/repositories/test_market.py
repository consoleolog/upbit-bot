import uuid
from unittest.mock import MagicMock

import asyncpg
import pytest

from src.repositories.errors import NotFoundError
from src.repositories.market import MarketRepository

repo = MarketRepository()


def _symbol() -> str:
    # 병렬로 실행되는 다른 테스트와 겹치지 않는 마켓 코드
    return "KRW-T" + uuid.uuid4().hex[:8].upper()


@pytest.mark.parametrize("symbol", ["KRWBTC", "KRW-", "-BTC", "", "KRW-BTC-X"])
async def test_add_rejects_malformed_symbol(symbol: str) -> None:
    with pytest.raises(ValueError, match="symbol"):
        await repo.add(MagicMock(), symbol)


@pytest.mark.integration
async def test_add_and_get(conn: asyncpg.Connection) -> None:
    symbol = _symbol()

    added = await repo.add(conn, symbol)

    assert added is not None
    assert (added.exchange, added.symbol, added.quote_currency) == ("upbit", symbol, "KRW")
    assert added.base_currency == symbol.removeprefix("KRW-")
    assert added.is_active
    assert await repo.get_by_symbol(conn, symbol) == added


@pytest.mark.integration
async def test_add_existing_returns_none(conn: asyncpg.Connection) -> None:
    symbol = _symbol()
    first = await repo.add(conn, symbol)

    assert await repo.add(conn, symbol) is None
    # 기존 행은 그대로이고, 트랜잭션도 계속 쓸 수 있다
    assert await repo.get_by_symbol(conn, symbol) == first


@pytest.mark.integration
async def test_add_rejects_unsupported_quote_currency(conn: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await repo.add(conn, "EUR-BTC")


@pytest.mark.integration
async def test_get_missing_market(conn: asyncpg.Connection) -> None:
    with pytest.raises(NotFoundError, match="KRW-NONE"):
        await repo.get_by_symbol(conn, "KRW-NONE")
