import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from pytest_mock import MockerFixture

from src.connections.database import Database, DatabaseConfig
from src.repositories.errors import NotFoundError
from src.repositories.market import MarketRepository
from src.repositories.market_halt import MarketHaltRepository
from src.repositories.models import MarketHalt
from src.resume_market import main, resume

T0 = datetime(2026, 11, 19, 3, 30, tzinfo=UTC)  # 한국 시간 11-19 12:30
HALT = MarketHalt(market_id=1, reason="order rejected (400 insufficient_funds_bid)", halted_at=T0)
DB_ENV = {"DB_HOST": "localhost", "DB_PORT": "5432", "DB_NAME": "db", "DB_USER": "user", "DB_PASSWORD": "pw"}
WEBHOOK = "https://discord.com/api/webhooks/123/secret-token-abc"


@pytest_asyncio.fixture
async def market(database: Database) -> AsyncIterator[tuple[str, int]]:
    """커밋된 마켓 하나. resume은 자기 연결로 커밋하므로 시험이 끝나면 직접 지운다."""
    symbol = "KRW-T" + uuid.uuid4().hex[:8].upper()
    async with database.transaction() as conn:
        added = await MarketRepository().add(conn, symbol)
    assert added is not None
    try:
        yield symbol, added.market_id
    finally:
        async with database.transaction() as conn:
            await conn.execute("DELETE FROM market_halt WHERE market_id = $1", added.market_id)
            await conn.execute("DELETE FROM market WHERE market_id = $1", added.market_id)


@pytest.mark.integration
async def test_resume_removes_halt(database: Database, test_db_config: DatabaseConfig, market: tuple[str, int]) -> None:
    symbol, market_id = market
    async with database.transaction() as conn:
        halt = await MarketHaltRepository().add(conn, market_id=market_id, reason="rejected", halted_at=T0)

    assert await resume(test_db_config, symbol) == halt
    async with database.transaction() as conn:
        assert market_id not in [halt.market_id for halt in await MarketHaltRepository().list_all(conn)]


@pytest.mark.integration
async def test_resume_not_halted_market(test_db_config: DatabaseConfig, market: tuple[str, int]) -> None:
    symbol, _ = market

    with pytest.raises(NotFoundError):
        await resume(test_db_config, symbol)


@pytest.mark.integration
async def test_resume_unknown_market(test_db_config: DatabaseConfig) -> None:
    with pytest.raises(NotFoundError):
        await resume(test_db_config, "KRW-NONE")


def test_main_without_database_env_fails(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    for key in DB_ENV:
        monkeypatch.delenv(key, raising=False)

    assert main(["KRW-BTC"]) == 1
    assert "DB_HOST" in capsys.readouterr().err


def test_main_without_market_exits() -> None:
    with pytest.raises(SystemExit):
        main([])


def _env(monkeypatch: pytest.MonkeyPatch, webhook: str | None) -> None:
    for key, value in DB_ENV.items():
        monkeypatch.setenv(key, value)
    if webhook is None:
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    else:
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", webhook)


def test_main_not_halted_fails(
    monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    _env(monkeypatch, WEBHOOK)
    mocker.patch("src.resume_market.resume", new_callable=mocker.AsyncMock, side_effect=NotFoundError("x"))
    send = mocker.patch("src.resume_market.DiscordNotifier.send_now", new_callable=mocker.AsyncMock)

    assert main(["KRW-BTC"]) == 1
    assert "중단된 마켓이 아닙니다" in capsys.readouterr().err
    send.assert_not_awaited()


def test_main_resumes_and_notifies(
    monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    _env(monkeypatch, WEBHOOK)
    mocker.patch("src.resume_market.resume", new_callable=mocker.AsyncMock, return_value=HALT)
    send = mocker.patch("src.resume_market.DiscordNotifier.send_now", new_callable=mocker.AsyncMock)

    assert main(["KRW-BTC"]) == 0

    out = capsys.readouterr().out
    assert "KRW-BTC: 중단 해제 (11-19 12:30부터 중단, 사유: order rejected" in out
    # 실행 중인 봇은 시작할 때 읽은 중단을 지키므로 재시작해야 한다
    assert "sudo docker compose restart bot" in out
    send.assert_awaited_once()
    assert send.await_args.args[0].startswith("✅ [중단 해제] KRW-BTC\n11-19 12:30부터 중단")


def test_main_without_webhook_does_not_notify(monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture) -> None:
    _env(monkeypatch, None)
    mocker.patch("src.resume_market.resume", new_callable=mocker.AsyncMock, return_value=HALT)
    send = mocker.patch("src.resume_market.DiscordNotifier.send_now", new_callable=mocker.AsyncMock)

    assert main(["KRW-BTC"]) == 0
    send.assert_not_awaited()


def test_main_with_invalid_webhook_still_resumes(
    monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    _env(monkeypatch, "https://example.com/hook/secret-token-abc")
    mocker.patch("src.resume_market.resume", new_callable=mocker.AsyncMock, return_value=HALT)

    # 해제는 이미 끝났으므로 알리지 못해도 성공이다. URL은 출력하지 않는다
    assert main(["KRW-BTC"]) == 0
    err = capsys.readouterr().err
    assert "알리지 못했습니다" in err
    assert "secret-token-abc" not in err
