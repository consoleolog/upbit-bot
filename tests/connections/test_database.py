from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.connections.database import PostgresPool

# ---------------------------------------------------------------------------
# FakeTransaction
# ---------------------------------------------------------------------------


class FakeTransaction:
    """asyncpg Transaction 비동기 컨텍스트 매니저 모사 클래스."""

    async def __aenter__(self) -> "FakeTransaction":
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# FakeConnection
# ---------------------------------------------------------------------------


class FakeConnection:
    """asyncpg Connection 모사 클래스."""

    def __init__(
        self,
        *,
        records: list[Any] | None = None,
        fetchrow_result: Any = None,
        execute_result: str = "INSERT 0 1",
        raise_on: set[str] | None = None,
    ) -> None:
        self._records = records or []
        self._fetchrow_result = fetchrow_result
        self._execute_result = execute_result
        self._raise_on = raise_on or set()
        self.last_query: str | None = None
        self.last_args: tuple = ()

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        self.last_query = query
        self.last_args = args
        if "fetch" in self._raise_on:
            raise RuntimeError("fetch 실패")
        return self._records

    async def fetchrow(self, query: str, *args: Any) -> Any | None:
        self.last_query = query
        self.last_args = args
        if "fetchrow" in self._raise_on:
            raise RuntimeError("fetchrow 실패")
        return self._fetchrow_result

    async def execute(self, query: str, *args: Any) -> str:
        self.last_query = query
        self.last_args = args
        if "execute" in self._raise_on:
            raise RuntimeError("execute 실패")
        return self._execute_result

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()


# ---------------------------------------------------------------------------
# _AcquireCtx
# ---------------------------------------------------------------------------


class _AcquireCtx:
    """pool.acquire() 비동기 컨텍스트 매니저 모사 클래스."""

    def __init__(self, conn: FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConnection:
        return self._conn

    async def __aexit__(self, *_: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# FakePostgresPool
# ---------------------------------------------------------------------------


class FakePostgresPool:
    """asyncpg Pool 모사 클래스."""

    def __init__(self, *, conn: FakeConnection | None = None) -> None:
        self.conn = conn or FakeConnection()
        self.closed = False

    def acquire(self) -> _AcquireCtx:
        return _AcquireCtx(self.conn)

    async def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# TestPostgresPoolInit
# ---------------------------------------------------------------------------


class TestPostgresPoolInit:
    def test_init_stores_config(self) -> None:
        """설정 딕셔너리가 self.config에 저장된다."""
        config = {"host": "localhost", "port": 5432}
        pg = PostgresPool(config)

        assert pg.config is config

    def test_init_pool_is_none(self) -> None:
        """초기 pool은 None이다."""
        pg = PostgresPool({})

        assert pg.pool is None

    def test_init_is_connected_false(self) -> None:
        """초기 is_connected는 False이다."""
        pg = PostgresPool({})

        assert pg.is_connected is False


# ---------------------------------------------------------------------------
# TestConnect
# ---------------------------------------------------------------------------


class TestConnect:
    async def test_connect_with_database_url(self, mocker) -> None:
        """database_url 설정으로 풀 생성 시 is_connected가 True가 된다."""
        fake_pool = FakePostgresPool()
        mocker.patch("asyncpg.create_pool", new=AsyncMock(return_value=fake_pool))

        pg = PostgresPool({"database_url": "postgresql://user:pass@localhost:5432/testdb"})
        await pg.connect()

        assert pg.is_connected is True
        assert pg.pool is fake_pool

    async def test_connect_with_database_url_parses_host(self, mocker) -> None:
        """database_url에서 파싱한 host가 create_pool에 전달된다."""
        mock_create = mocker.patch("asyncpg.create_pool", new=AsyncMock(return_value=FakePostgresPool()))

        pg = PostgresPool({"database_url": "postgresql://myuser:mypass@myhost:5555/mydb"})
        await pg.connect()

        _, kwargs = mock_create.call_args
        assert kwargs["host"] == "myhost"
        assert kwargs["port"] == 5555
        assert kwargs["user"] == "myuser"
        assert kwargs["password"] == "mypass"
        assert kwargs["database"] == "mydb"

    async def test_connect_with_individual_params(self, mocker) -> None:
        """개별 파라미터 설정으로 풀 생성 시 is_connected가 True가 된다."""
        mock_create = mocker.patch("asyncpg.create_pool", new=AsyncMock(return_value=FakePostgresPool()))

        pg = PostgresPool({"host": "myhost", "port": 5433, "user": "admin", "password": "secret", "database": "mydb"})
        await pg.connect()

        assert pg.is_connected is True
        _, kwargs = mock_create.call_args
        assert kwargs["host"] == "myhost"
        assert kwargs["port"] == 5433
        assert kwargs["user"] == "admin"
        assert kwargs["password"] == "secret"
        assert kwargs["database"] == "mydb"

    async def test_connect_with_default_params(self, mocker) -> None:
        """설정이 없으면 기본값(host=postgres)으로 create_pool이 호출된다."""
        mock_create = mocker.patch("asyncpg.create_pool", new=AsyncMock(return_value=FakePostgresPool()))

        pg = PostgresPool({})
        await pg.connect()

        _, kwargs = mock_create.call_args
        assert kwargs["host"] == "postgres"
        assert kwargs["port"] == 5432
        assert kwargs["user"] == "postgres"
        assert kwargs["database"] == "postgres"

    async def test_connect_pool_kwargs_passed(self, mocker) -> None:
        """풀 설정 파라미터가 create_pool에 올바르게 전달된다."""
        mock_create = mocker.patch("asyncpg.create_pool", new=AsyncMock(return_value=FakePostgresPool()))

        pg = PostgresPool(
            {"pool_min": 5, "pool_max": 15, "max_queries": 1000, "connection_lifetime": 120, "command_timeout": 30}
        )
        await pg.connect()

        _, kwargs = mock_create.call_args
        assert kwargs["min_size"] == 5
        assert kwargs["max_size"] == 15
        assert kwargs["max_queries"] == 1000
        assert kwargs["max_inactive_connection_lifetime"] == 120
        assert kwargs["command_timeout"] == 30

    async def test_connect_default_pool_kwargs(self, mocker) -> None:
        """풀 설정을 지정하지 않으면 기본값이 create_pool에 전달된다."""
        mock_create = mocker.patch("asyncpg.create_pool", new=AsyncMock(return_value=FakePostgresPool()))

        pg = PostgresPool({})
        await pg.connect()

        _, kwargs = mock_create.call_args
        assert kwargs["min_size"] == 10
        assert kwargs["max_size"] == 20
        assert kwargs["max_queries"] == 50000
        assert kwargs["max_inactive_connection_lifetime"] == 300
        assert kwargs["command_timeout"] == 60

    async def test_connect_raises_on_failure(self, mocker) -> None:
        """asyncpg.create_pool이 예외를 던지면 connect도 예외를 전파한다."""
        mocker.patch("asyncpg.create_pool", side_effect=Exception("연결 실패"))

        pg = PostgresPool({"database_url": "postgresql://user:pass@localhost:5432/testdb"})
        with pytest.raises(Exception, match="연결 실패"):
            await pg.connect()

        assert pg.is_connected is False


# ---------------------------------------------------------------------------
# TestDisconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    async def test_disconnect_closes_pool(self) -> None:
        """disconnect 시 풀의 close가 호출된다."""
        fake_pool = FakePostgresPool()
        pg = PostgresPool({})
        pg.pool = fake_pool
        pg.is_connected = True

        await pg.disconnect()

        assert fake_pool.closed is True

    async def test_disconnect_sets_pool_none(self) -> None:
        """disconnect 후 pool이 None이 된다."""
        pg = PostgresPool({})
        pg.pool = FakePostgresPool()
        pg.is_connected = True

        await pg.disconnect()

        assert pg.pool is None

    async def test_disconnect_sets_is_connected_false(self) -> None:
        """disconnect 후 is_connected가 False가 된다."""
        pg = PostgresPool({})
        pg.pool = FakePostgresPool()
        pg.is_connected = True

        await pg.disconnect()

        assert pg.is_connected is False

    async def test_disconnect_noop_when_pool_is_none(self) -> None:
        """pool이 None일 때 disconnect를 호출해도 예외가 발생하지 않는다."""
        pg = PostgresPool({})
        await pg.disconnect()


# ---------------------------------------------------------------------------
# TestAcquire
# ---------------------------------------------------------------------------


class TestAcquire:
    async def test_acquire_yields_connection(self) -> None:
        """acquire는 커넥션을 yield한다."""
        fake_pool = FakePostgresPool()
        pg = PostgresPool({})
        pg.pool = fake_pool

        async with pg.acquire() as conn:
            assert conn is fake_pool.conn

    async def test_acquire_raises_when_pool_is_none(self) -> None:
        """pool이 None이면 RuntimeError가 발생한다."""
        pg = PostgresPool({})

        with pytest.raises(RuntimeError, match="데이터베이스가 연결되지 않았습니다"):
            async with pg.acquire():
                pass


# ---------------------------------------------------------------------------
# TestTransaction
# ---------------------------------------------------------------------------


class TestTransaction:
    async def test_transaction_yields_connection(self) -> None:
        """transaction은 커넥션을 yield한다."""
        fake_pool = FakePostgresPool()
        pg = PostgresPool({})
        pg.pool = fake_pool

        async with pg.transaction() as conn:
            assert conn is fake_pool.conn

    async def test_transaction_raises_when_pool_is_none(self) -> None:
        """pool이 None이면 RuntimeError가 발생한다."""
        pg = PostgresPool({})

        with pytest.raises(RuntimeError, match="데이터베이스가 연결되지 않았습니다"):
            async with pg.transaction():
                pass

    async def test_transaction_propagates_exception(self) -> None:
        """트랜잭션 블록에서 예외가 발생하면 예외가 전파된다."""
        pg = PostgresPool({})
        pg.pool = FakePostgresPool()

        with pytest.raises(ValueError, match="테스트 예외"):
            async with pg.transaction():
                raise ValueError("테스트 예외")


# ---------------------------------------------------------------------------
# TestFetchAll
# ---------------------------------------------------------------------------


class TestFetchAll:
    async def test_fetch_all_returns_records(self) -> None:
        """연결된 상태에서 쿼리 결과 리스트를 반환한다."""
        records = [{"id": 1}, {"id": 2}]
        pg = PostgresPool({})
        pg.pool = FakePostgresPool(conn=FakeConnection(records=records))
        pg.is_connected = True

        result = await pg.fetch_all("SELECT * FROM users")

        assert result == records

    async def test_fetch_all_returns_empty_when_pool_none(self) -> None:
        """pool이 None이면 빈 리스트를 반환한다."""
        pg = PostgresPool({})

        result = await pg.fetch_all("SELECT * FROM users")

        assert result == []

    async def test_fetch_all_returns_empty_when_not_connected(self) -> None:
        """is_connected가 False이면 빈 리스트를 반환한다."""
        pg = PostgresPool({})
        pg.pool = FakePostgresPool()
        pg.is_connected = False

        result = await pg.fetch_all("SELECT * FROM users")

        assert result == []

    async def test_fetch_all_passes_args(self) -> None:
        """쿼리 파라미터가 올바르게 전달된다."""
        conn = FakeConnection(records=[{"id": 1}])
        pg = PostgresPool({})
        pg.pool = FakePostgresPool(conn=conn)
        pg.is_connected = True

        await pg.fetch_all("SELECT * FROM users WHERE id = $1", 42)

        assert conn.last_args == (42,)

    async def test_fetch_all_returns_empty_list_on_no_rows(self) -> None:
        """결과가 없으면 빈 리스트를 반환한다."""
        pg = PostgresPool({})
        pg.pool = FakePostgresPool(conn=FakeConnection(records=[]))
        pg.is_connected = True

        result = await pg.fetch_all("SELECT * FROM users WHERE 1=0")

        assert result == []


# ---------------------------------------------------------------------------
# TestFetchOne
# ---------------------------------------------------------------------------


class TestFetchOne:
    async def test_fetch_one_returns_record(self) -> None:
        """연결된 상태에서 첫 번째 결과 행을 반환한다."""
        record = {"id": 1, "name": "홍길동"}
        pg = PostgresPool({})
        pg.pool = FakePostgresPool(conn=FakeConnection(fetchrow_result=record))
        pg.is_connected = True

        result = await pg.fetch_one("SELECT * FROM users WHERE id = $1", 1)

        assert result == record

    async def test_fetch_one_returns_none_when_pool_none(self) -> None:
        """pool이 None이면 None을 반환한다."""
        pg = PostgresPool({})

        result = await pg.fetch_one("SELECT * FROM users WHERE id = $1", 1)

        assert result is None

    async def test_fetch_one_returns_none_when_not_connected(self) -> None:
        """is_connected가 False이면 None을 반환한다."""
        pg = PostgresPool({})
        pg.pool = FakePostgresPool()
        pg.is_connected = False

        result = await pg.fetch_one("SELECT * FROM users WHERE id = $1", 1)

        assert result is None

    async def test_fetch_one_returns_none_when_no_rows(self) -> None:
        """결과가 없으면 None을 반환한다."""
        pg = PostgresPool({})
        pg.pool = FakePostgresPool(conn=FakeConnection(fetchrow_result=None))
        pg.is_connected = True

        result = await pg.fetch_one("SELECT * FROM users WHERE id = $1", 999)

        assert result is None

    async def test_fetch_one_passes_args(self) -> None:
        """쿼리 파라미터가 올바르게 전달된다."""
        conn = FakeConnection(fetchrow_result={"id": 1})
        pg = PostgresPool({})
        pg.pool = FakePostgresPool(conn=conn)
        pg.is_connected = True

        await pg.fetch_one("SELECT * FROM users WHERE id = $1 AND name = $2", 1, "test")

        assert conn.last_args == (1, "test")


# ---------------------------------------------------------------------------
# TestExecute
# ---------------------------------------------------------------------------


class TestExecute:
    async def test_execute_returns_status(self) -> None:
        """연결된 상태에서 명령 상태 문자열을 반환한다."""
        pg = PostgresPool({})
        pg.pool = FakePostgresPool(conn=FakeConnection(execute_result="INSERT 0 1"))
        pg.is_connected = True

        result = await pg.execute("INSERT INTO users VALUES ($1)", "홍길동")

        assert result == "INSERT 0 1"

    async def test_execute_returns_none_when_pool_none(self) -> None:
        """pool이 None이면 None을 반환한다."""
        pg = PostgresPool({})

        result = await pg.execute("INSERT INTO users VALUES ($1)", "홍길동")

        assert result is None

    async def test_execute_returns_none_when_not_connected(self) -> None:
        """is_connected가 False이면 None을 반환한다."""
        pg = PostgresPool({})
        pg.pool = FakePostgresPool()
        pg.is_connected = False

        result = await pg.execute("DELETE FROM users WHERE id = $1", 1)

        assert result is None

    async def test_execute_passes_args(self) -> None:
        """쿼리 파라미터가 올바르게 전달된다."""
        conn = FakeConnection()
        pg = PostgresPool({})
        pg.pool = FakePostgresPool(conn=conn)
        pg.is_connected = True

        await pg.execute("UPDATE users SET name = $1 WHERE id = $2", "김철수", 5)

        assert conn.last_args == ("김철수", 5)

    async def test_execute_update_status(self) -> None:
        """UPDATE 쿼리의 명령 상태를 반환한다."""
        pg = PostgresPool({})
        pg.pool = FakePostgresPool(conn=FakeConnection(execute_result="UPDATE 3"))
        pg.is_connected = True

        result = await pg.execute("UPDATE users SET active = $1", True)

        assert result == "UPDATE 3"

    async def test_execute_delete_status(self) -> None:
        """DELETE 쿼리의 명령 상태를 반환한다."""
        pg = PostgresPool({})
        pg.pool = FakePostgresPool(conn=FakeConnection(execute_result="DELETE 1"))
        pg.is_connected = True

        result = await pg.execute("DELETE FROM users WHERE id = $1", 1)

        assert result == "DELETE 1"
