import os
from collections.abc import AsyncGenerator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from faker import Faker
from freezegun import freeze_time as _freeze_time

from src.database import Database, DatabaseConfig

# ---------------------------------------------------------------------------
# Faker
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def faker() -> Faker:
    """가짜 데이터 생성기 (세션 전체 공유)"""
    return Faker("ko_KR")


# ---------------------------------------------------------------------------
# Freezegun
# ---------------------------------------------------------------------------


@pytest.fixture
def frozen_time():
    """날짜/시간을 고정하는 컨텍스트 매니저를 반환합니다.

    Usage:
        def test_something(frozen_time):
            with frozen_time("2024-01-01 00:00:00"):
                ...
    """
    return _freeze_time


# ---------------------------------------------------------------------------
# 환경 변수
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_env(monkeypatch: pytest.MonkeyPatch):
    """환경 변수를 임시로 설정하고 테스트 종료 후 자동 복원합니다.

    Usage:
        def test_something(mock_env):
            mock_env({"API_KEY": "test-key", "DEBUG": "true"})
            ...
    """

    def _set(env_vars: dict[str, str]) -> None:
        for key, value in env_vars.items():
            monkeypatch.setenv(key, value)

    return _set


# ---------------------------------------------------------------------------
# HTTP Client (async)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """비동기 HTTP 클라이언트 (테스트마다 새로 생성)"""
    async with httpx.AsyncClient() as client:
        yield client


# ---------------------------------------------------------------------------
# 테스트 DB (통합 테스트)
# ---------------------------------------------------------------------------

SCHEMA = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
# 여러 xdist 워커가 동시에 스키마를 적용하지 않게 막는 advisory lock 키
SCHEMA_LOCK_KEY = 20260001


@pytest.fixture(scope="session")
def test_db_config() -> DatabaseConfig:
    """TEST_DB_* 환경 변수로 만든 테스트 DB 설정. 없으면 통합 테스트를 건너뛴다. 운영 설정 DB_*는 읽지 않는다."""
    if not os.environ.get("TEST_DB_HOST"):
        pytest.skip("TEST_DB_* is not set")
    return DatabaseConfig.from_env("TEST_DB_")


@pytest_asyncio.fixture
async def database(test_db_config: DatabaseConfig) -> AsyncGenerator[Database, None]:
    """연결된 테스트 DB. 스키마가 없을 때만 db/schema.sql을 적용한다."""
    db = Database(test_db_config)
    await db.connect()
    try:
        async with db.transaction() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock($1)", SCHEMA_LOCK_KEY)
            if await conn.fetchval("SELECT to_regclass('market')") is None:
                await conn.execute(SCHEMA.read_text(encoding="utf-8"))
        yield db
    finally:
        await db.close()
