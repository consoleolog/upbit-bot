"""PostgreSQL 연결. 커넥션 풀을 두고, 작업마다 커넥션 1개로 트랜잭션을 연다."""

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Self

import asyncpg

logger = logging.getLogger(__name__)

# 풀을 닫을 때 빌려간 커넥션이 반납되기를 기다리는 최대 시간
CLOSE_TIMEOUT = 10.0


@dataclass(frozen=True, kw_only=True)
class DatabaseConfig:
    host: str
    port: int
    name: str
    user: str
    password: str = field(repr=False)
    pool_min_size: int = 2
    pool_max_size: int = 10
    connect_timeout: float = 10.0
    acquire_timeout: float = 10.0
    command_timeout: float = 30.0

    def __post_init__(self) -> None:
        # 비밀번호가 메시지에 들어가지 않도록 repr(self)만 쓴다
        if not (self.host and self.name and self.user and self.password):
            raise ValueError(f"database host, name, user and password must not be empty: {self}")
        if not 1 <= self.port <= 65535:
            raise ValueError(f"database port out of range: {self}")
        if not 1 <= self.pool_min_size <= self.pool_max_size:
            raise ValueError(f"database pool sizes must satisfy 1 <= min <= max: {self}")
        if min(self.connect_timeout, self.acquire_timeout, self.command_timeout) <= 0:
            raise ValueError(f"database timeouts must be positive: {self}")

    @classmethod
    def from_env(cls, prefix: str, env: Mapping[str, str] = os.environ) -> Self:
        """{prefix}HOST, PORT, NAME, USER, PASSWORD 환경 변수에서 읽는다. 운영은 DB_, 시험은 TEST_DB_."""
        keys = [f"{prefix}{key}" for key in ("HOST", "PORT", "NAME", "USER", "PASSWORD")]
        missing = [key for key in keys if not env.get(key)]
        if missing:
            raise ValueError(f"missing environment variables: {missing}")
        host, port, name, user, password = (env[key] for key in keys)
        if not port.isdigit():
            raise ValueError(f"{prefix}PORT must be a number: {port!r}")
        return cls(host=host, port=int(port), name=name, user=user, password=password)


class Database:
    def __init__(self, config: DatabaseConfig) -> None:
        self._config = config
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        """풀을 만들고 SELECT 1로 연결을 확인한다. 실패하면 예외를 던진다."""
        if self._pool is not None:
            raise RuntimeError("database is already connected")
        c = self._config
        pool = await asyncpg.create_pool(
            host=c.host,
            port=c.port,
            database=c.name,
            user=c.user,
            password=c.password,
            min_size=c.pool_min_size,
            max_size=c.pool_max_size,
            timeout=c.connect_timeout,
            command_timeout=c.command_timeout,
            server_settings={"application_name": "upbit-bot"},
        )
        try:
            async with pool.acquire(timeout=c.acquire_timeout) as conn:
                await conn.fetchval("SELECT 1")
        except BaseException:
            # 확인에 실패한 풀은 기다리지 않고 바로 끊는다
            pool.terminate()
            raise
        self._pool = pool
        logger.info("database connected (host=%s, port=%d, name=%s)", c.host, c.port, c.name)

    async def close(self) -> None:
        """풀을 닫는다. 빌려간 커넥션이 CLOSE_TIMEOUT 안에 반납되지 않으면 강제로 끊는다."""
        if self._pool is None:
            return
        pool, self._pool = self._pool, None
        try:
            async with asyncio.timeout(CLOSE_TIMEOUT):
                await pool.close()
        except TimeoutError:
            logger.warning("database close timed out; terminating connections")
            pool.terminate()
        logger.info("database closed")

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        """커넥션 1개를 빌려 트랜잭션을 연다. 정상 종료면 커밋, 예외면 롤백 후 다시 던진다."""
        if self._pool is None:
            raise RuntimeError("database is not connected")
        async with self._pool.acquire(timeout=self._config.acquire_timeout) as conn, conn.transaction():
            yield conn
