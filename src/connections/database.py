import urllib.parse
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import asyncpg
import structlog
from asyncpg.pool import Pool

logger = structlog.get_logger(__name__)


class PostgresPool:
    """asyncpg 기반 PostgreSQL 커넥션 풀 관리 클래스."""

    def __init__(self, config: dict) -> None:
        """설정 딕셔너리로 초기화합니다.

        Args:
            config: 연결 및 풀 설정 딕셔너리. 지원하는 키:

                - ``database_url`` -전체 연결 URL
                  (``postgresql://user:pass@host:port/dbname``).
                - ``host``, ``port``, ``user``, ``password``, ``database`` -
                  ``database_url`` 이 없을 때 사용하는 개별 연결 파라미터.
                - ``pool_min`` (int, 기본값 10) -최소 풀 크기.
                - ``pool_max`` (int, 기본값 20) -최대 풀 크기.
                - ``max_queries`` (int, 기본값 50000) -커넥션당 최대 쿼리 수
                  (초과 시 커넥션 재생성).
                - ``connection_lifetime`` (int, 기본값 300) -유휴 커넥션
                  유지 시간(초).
                - ``command_timeout`` (int, 기본값 60) -기본 명령 타임아웃(초).
        """
        self.config = config
        self.pool: Pool | None = None
        self.is_connected: bool = False

    async def connect(self) -> None:
        """커넥션 풀을 생성하고 데이터베이스에 연결."""
        try:
            database_url: str | None = self.config.get("database_url")

            pool_kwargs = {
                "min_size": self.config.get("pool_min", 10),
                "max_size": self.config.get("pool_max", 20),
                "max_queries": self.config.get("max_queries", 50000),
                "max_inactive_connection_lifetime": self.config.get("connection_lifetime", 300),
                "command_timeout": self.config.get("command_timeout", 60),
            }

            if database_url:
                parsed = urllib.parse.urlparse(database_url)
                logger.info(
                    "🔌 데이터베이스 연결 중", host=parsed.hostname, port=parsed.port, database=parsed.path.lstrip("/")
                )
                self.pool = await asyncpg.create_pool(
                    host=parsed.hostname,
                    port=parsed.port,
                    user=parsed.username,
                    password=parsed.password,
                    database=parsed.path.lstrip("/"),
                    **pool_kwargs,
                )
            else:
                host = self.config.get("host", "postgres")
                port = self.config.get("port", 5432)
                user = self.config.get("user", "postgres")
                database = self.config.get("database", "postgres")
                password = self.config.get("password", "")

                logger.info("🔌 데이터베이스 연결 중", host=host, port=port)
                self.pool = await asyncpg.create_pool(
                    host=host, port=port, user=user, password=password, database=database, **pool_kwargs
                )

            self.is_connected = True
            logger.info("✅ PostgreSQL 데이터베이스 연결 성공")
        except Exception as error:
            logger.exception("❌ 데이터베이스 연결 실패", error=str(error))
            raise

    async def disconnect(self) -> None:
        """풀 내 모든 커넥션을 닫고 연결 상태를 해제."""
        if self.pool:
            await self.pool.close()
            self.pool = None
            self.is_connected = False
            logger.info("🔒 데이터베이스 연결 해제")

    @asynccontextmanager
    async def acquire(self) -> AsyncGenerator[asyncpg.Connection, None]:
        """풀에서 커넥션 하나를 획득합니다.

        명시적 트랜잭션이 필요 없는 단순 쿼리에 사용합니다.
        컨텍스트 블록 종료 시 커넥션은 자동으로 풀에 반환됩니다.

        Yields:
            asyncpg.Connection: 활성 데이터베이스 커넥션.

        Raises:
            RuntimeError: 풀이 연결되지 않은 경우.
        """
        if not self.pool:
            raise RuntimeError("데이터베이스가 연결되지 않았습니다.")
        async with self.pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[asyncpg.Connection, None]:
        """커넥션을 획득하고 데이터베이스 트랜잭션을 시작합니다.

        ``async with`` 블록이 정상 종료되면 커밋, 예외 발생 시 자동 롤백합니다.

        Yields:
            asyncpg.Connection: 트랜잭션이 열려 있는 활성 데이터베이스 커넥션.

        Raises:
            RuntimeError: 풀이 연결되지 않은 경우.
        """
        if not self.pool:
            raise RuntimeError("데이터베이스가 연결되지 않았습니다.")
        async with self.pool.acquire() as conn, conn.transaction():
            yield conn

    async def fetch_all(self, query: str, *args) -> list[asyncpg.Record]:
        """쿼리를 실행하고 결과 행 전체를 반환합니다.

        풀이 연결되지 않은 경우 빈 리스트를 반환합니다.

        Args:
            query: 실행할 SQL 쿼리 문자열.
            *args: 쿼리 바인딩 파라미터.

        Returns:
            list: 결과 행 목록. 연결되지 않은 경우 빈 리스트.
        """
        if not self.pool or not self.is_connected:
            return []

        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetch_one(self, query: str, *args) -> asyncpg.Record | None:
        """쿼리를 실행하고 첫 번째 결과 행 하나를 반환합니다.

        풀이 연결되지 않은 경우 None을 반환합니다.

        Args:
            query: 실행할 SQL 쿼리 문자열.
            *args: 쿼리 바인딩 파라미터.

        Returns:
            asyncpg.Record | None: 첫 번째 결과 행. 결과가 없거나 연결되지 않은 경우 None.
        """
        if not self.pool or not self.is_connected:
            return None

        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def execute(self, query: str, *args) -> str | None:
        """INSERT, UPDATE, DELETE 등 쿼리를 실행합니다.

        풀이 연결되지 않은 경우 None을 반환합니다.

        Args:
            query: 실행할 SQL 쿼리 문자열.
            *args: 쿼리 바인딩 파라미터.

        Returns:
            str | None: 명령 상태 문자열 (예: ``INSERT 0 1``, ``UPDATE 3``).
                연결되지 않은 경우 None.
        """
        if not self.pool or not self.is_connected:
            return None

        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)
