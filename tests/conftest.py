from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from faker import Faker
from freezegun import freeze_time as _freeze_time

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
