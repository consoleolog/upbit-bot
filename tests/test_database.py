import asyncio
import uuid
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from pytest_mock import MockerFixture

from src import database
from src.database import Database, DatabaseConfig

ENV = {
    "TEST_DB_HOST": "localhost",
    "TEST_DB_PORT": "5432",
    "TEST_DB_NAME": "upbit_bot_test",
    "TEST_DB_USER": "tester",
    "TEST_DB_PASSWORD": "s3cret",
}


def _config(**overrides: object) -> DatabaseConfig:
    values: dict[str, object] = {"host": "localhost", "port": 5432, "name": "db", "user": "u", "password": "s3cret"}
    return DatabaseConfig(**(values | overrides))  # type: ignore[arg-type]


def test_config_from_env() -> None:
    config = DatabaseConfig.from_env("TEST_DB_", ENV)

    assert (config.host, config.port, config.name, config.user) == ("localhost", 5432, "upbit_bot_test", "tester")
    assert config.password == "s3cret"


def test_config_from_env_reads_only_given_prefix() -> None:
    # 운영 설정(DB_*)만 있으면 시험 설정으로 읽지 않는다
    env = {key.removeprefix("TEST_"): value for key, value in ENV.items()}

    with pytest.raises(ValueError, match="TEST_DB_HOST"):
        DatabaseConfig.from_env("TEST_DB_", env)


@pytest.mark.parametrize("key", ["TEST_DB_HOST", "TEST_DB_PASSWORD"])
def test_config_from_env_rejects_missing_or_empty(key: str) -> None:
    with pytest.raises(ValueError, match=key):
        DatabaseConfig.from_env("TEST_DB_", ENV | {key: ""})


@pytest.mark.parametrize("port", ["abc", "-1", "5432.0", ""])
def test_config_from_env_rejects_bad_port(port: str) -> None:
    with pytest.raises(ValueError, match="TEST_DB_PORT"):
        DatabaseConfig.from_env("TEST_DB_", ENV | {"TEST_DB_PORT": port})


@pytest.mark.parametrize(
    "overrides",
    [
        {"host": ""},
        {"password": ""},
        {"port": 0},
        {"port": 65536},
        {"pool_min_size": 0},
        {"pool_min_size": 3, "pool_max_size": 2},
        {"acquire_timeout": 0},
        {"command_timeout": -1},
    ],
)
def test_invalid_config_rejected_without_leaking_password(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="database") as error:
        _config(**overrides)

    assert "s3cret" not in str(error.value)


def test_config_repr_hides_password() -> None:
    assert "s3cret" not in repr(_config())


async def test_transaction_before_connect_fails() -> None:
    with pytest.raises(RuntimeError, match="not connected"):
        async with Database(_config()).transaction():
            pass


async def test_close_without_connect_does_nothing() -> None:
    await Database(_config()).close()


def _fake_pool(mocker: MockerFixture, *, select_error: BaseException | None = None) -> MagicMock:
    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=select_error, return_value=1)
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=conn)
    acquire.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire.return_value = acquire
    pool.close = AsyncMock()
    mocker.patch.object(database.asyncpg, "create_pool", AsyncMock(return_value=pool))
    return pool


async def test_connect_failure_terminates_pool(mocker: MockerFixture) -> None:
    pool = _fake_pool(mocker, select_error=OSError("connection lost"))
    db = Database(_config())

    with pytest.raises(OSError, match="connection lost"):
        await db.connect()

    pool.terminate.assert_called_once()
    # 연결되지 않은 상태로 남는다
    with pytest.raises(RuntimeError, match="not connected"):
        async with db.transaction():
            pass


async def test_connect_twice_fails(mocker: MockerFixture) -> None:
    _fake_pool(mocker)
    db = Database(_config())
    await db.connect()

    with pytest.raises(RuntimeError, match="already connected"):
        await db.connect()


async def test_close_terminates_when_connections_are_not_returned(mocker: MockerFixture) -> None:
    pool = _fake_pool(mocker)

    async def never_returns() -> None:
        await asyncio.Event().wait()

    pool.close = AsyncMock(side_effect=never_returns)
    mocker.patch.object(database, "CLOSE_TIMEOUT", 0.01)
    db = Database(_config())
    await db.connect()

    await db.close()

    pool.terminate.assert_called_once()


# ---------------------------------------------------------------------------
# 실제 PostgreSQL (TEST_DB_* 가 없으면 건너뜀)
# ---------------------------------------------------------------------------


def _symbol() -> tuple[str, str]:
    base = "T" + uuid.uuid4().hex[:8].upper()
    return f"KRW-{base}", base


async def _count(db: Database, symbol: str) -> int:
    async with db.transaction() as conn:
        return await conn.fetchval("SELECT count(*) FROM market WHERE symbol = $1", symbol)


@pytest.mark.integration
async def test_transaction_commits(database: Database) -> None:
    symbol, base = _symbol()
    try:
        async with database.transaction() as conn:
            await conn.execute(
                "INSERT INTO market (symbol, base_currency, quote_currency) VALUES ($1, $2, 'KRW')", symbol, base
            )

        # 다른 커넥션에서도 보인다
        assert await _count(database, symbol) == 1
    finally:
        async with database.transaction() as conn:
            await conn.execute("DELETE FROM market WHERE symbol = $1", symbol)


@pytest.mark.integration
async def test_transaction_rolls_back_on_error(database: Database) -> None:
    symbol, base = _symbol()

    with pytest.raises(RuntimeError, match="boom"):
        async with database.transaction() as conn:
            await conn.execute(
                "INSERT INTO market (symbol, base_currency, quote_currency) VALUES ($1, $2, 'KRW')", symbol, base
            )
            raise RuntimeError("boom")

    assert await _count(database, symbol) == 0


@pytest.mark.integration
async def test_constraint_violation_rolls_back(database: Database) -> None:
    symbol, base = _symbol()

    with pytest.raises(asyncpg.CheckViolationError):
        async with database.transaction() as conn:
            await conn.execute(
                "INSERT INTO market (symbol, base_currency, quote_currency) VALUES ($1, $2, 'KRW')", symbol, base
            )
            # 마켓 코드와 통화가 어긋나 거부된다
            await conn.execute(
                "INSERT INTO market (symbol, base_currency, quote_currency) VALUES ($1, 'BTC', 'KRW')", symbol + "X"
            )

    assert await _count(database, symbol) == 0


@pytest.mark.integration
async def test_connect_with_wrong_password_fails(test_db_config: DatabaseConfig) -> None:
    db = Database(replace(test_db_config, password="wrong"))

    with pytest.raises(asyncpg.InvalidPasswordError):
        await db.connect()
