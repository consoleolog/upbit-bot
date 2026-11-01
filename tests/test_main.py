import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from src.app.application import Application
from src.app.config import Timeframe, TradingConfig
from src.connections.database import Database, DatabaseConfig
from src.exchange.account import Account, OrderChance
from src.exchange.upbit_auth import UpbitCredentials
from src.exchange.upbit_client import UpbitAuthError
from src.main import EXIT_OK, EXIT_STARTUP_FAILED, StartupError, main, parse_args, register_markets, run
from src.trading.indicators import IndicatorSettings
from src.trading.signals import SignalSettings, Threshold


def test_parse_args_reads_config_path() -> None:
    args = parse_args(["--config", "config/trading.json"])

    assert args.config == Path("config/trading.json")


def test_parse_args_without_config_exits() -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_args([])

    assert exc_info.value.code == 2


def test_main_fails_when_config_file_missing(tmp_path: Path, mocker: MockerFixture) -> None:
    run = mocker.patch("src.main.Application.run")

    code = main(["--config", str(tmp_path / "missing.json")])

    assert code == EXIT_STARTUP_FAILED
    run.assert_not_called()


def test_main_fails_when_config_path_is_directory(tmp_path: Path, mocker: MockerFixture) -> None:
    run = mocker.patch("src.main.Application.run")

    code = main(["--config", str(tmp_path)])

    assert code == EXIT_STARTUP_FAILED
    run.assert_not_called()


def test_main_fails_when_config_invalid(tmp_path: Path, mocker: MockerFixture) -> None:
    config = tmp_path / "config.json"
    config.write_text('{"timeframe": "2m", "markets": ["KRW-BTC"]}', encoding="utf-8")
    run = mocker.patch("src.main.Application.run")

    code = main(["--config", str(config)])

    assert code == EXIT_STARTUP_FAILED
    run.assert_not_called()


DB_ENV = {"DB_HOST": "localhost", "DB_PORT": "5432", "DB_NAME": "upbit_bot", "DB_USER": "bot", "DB_PASSWORD": "pw"}
DB_CONFIG = DatabaseConfig.from_env("DB_", DB_ENV)


def test_main_fails_when_database_env_missing(
    tmp_path: Path, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.json"
    config.write_text('{"timeframe": "30m", "markets": ["KRW-BTC"]}', encoding="utf-8")
    for key in DB_ENV:
        monkeypatch.delenv(key, raising=False)
    connect = mocker.patch("src.main.Database.connect")

    code = main(["--config", str(config)])

    assert code == EXIT_STARTUP_FAILED
    connect.assert_not_called()


def test_main_runs_application_with_scheduler(
    tmp_path: Path, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.json"
    config.write_text('{"timeframe": "30m", "markets": ["KRW-BTC"]}', encoding="utf-8")
    for key, value in DB_ENV.items():
        monkeypatch.setenv(key, value)
    mocker.patch("src.main.Database.connect", new_callable=mocker.AsyncMock)
    mocker.patch("src.main.Database.close", new_callable=mocker.AsyncMock)
    mocker.patch("src.main.register_markets", new_callable=mocker.AsyncMock, return_value={"KRW-BTC": 1})
    run = mocker.patch("src.main.Application.run", new_callable=mocker.AsyncMock)
    add_service = mocker.spy(Application, "add_service")

    code = main(["--config", str(config)])

    assert code == EXIT_OK
    run.assert_awaited_once()
    assert [call.args[1] for call in add_service.call_args_list] == ["scheduler:30m", "ticker"]


def _record(mocker: MockerFixture, target: str, events: list[str], name: str, **kwargs: object) -> None:
    mocker.patch(target, side_effect=lambda *_: events.append(name), new_callable=mocker.AsyncMock, **kwargs)


async def test_run_closes_connections_after_application_stops(mocker: MockerFixture) -> None:
    events: list[str] = []
    _record(mocker, "src.main.Database.connect", events, "db open")
    _record(mocker, "src.main.Database.close", events, "db close")
    mocker.patch("src.main.register_markets", new_callable=mocker.AsyncMock, return_value={"KRW-BTC": 1})
    _record(mocker, "src.main.UpbitClient.open", events, "upbit open")
    _record(mocker, "src.main.UpbitClient.close", events, "upbit close")
    _record(mocker, "src.main.Application.run", events, "run")

    await run(Path("config.json"), TradingConfig(timeframe=Timeframe.MINUTE_30, markets=("KRW-BTC",)), DB_CONFIG)

    assert events == ["db open", "upbit open", "run", "upbit close", "db close"]


async def test_run_passes_configured_settings_to_worker(mocker: MockerFixture) -> None:
    mocker.patch("src.main.Database.connect", new_callable=mocker.AsyncMock)
    mocker.patch("src.main.Database.close", new_callable=mocker.AsyncMock)
    mocker.patch("src.main.register_markets", new_callable=mocker.AsyncMock, return_value={"KRW-BTC": 1})
    mocker.patch("src.main.Application.run", new_callable=mocker.AsyncMock)
    worker = mocker.patch("src.main.MarketWorker")
    config = TradingConfig(
        timeframe=Timeframe.MINUTE_30,
        markets=("KRW-BTC",),
        indicator=IndicatorSettings(rsi_period=10),
        signal=SignalSettings(rsi=Threshold(over_sold=20, over_bought=80)),
    )

    await run(Path("config.json"), config, DB_CONFIG)

    kwargs = worker.call_args.kwargs
    assert kwargs["indicator_settings"] is config.indicator
    assert kwargs["signal_settings"] is config.signal


async def test_run_feeds_tickers_to_realtime_exit(mocker: MockerFixture) -> None:
    mocker.patch("src.main.Database.connect", new_callable=mocker.AsyncMock)
    mocker.patch("src.main.Database.close", new_callable=mocker.AsyncMock)
    mocker.patch("src.main.register_markets", new_callable=mocker.AsyncMock, return_value={"KRW-BTC": 1})
    mocker.patch("src.main.Application.run", new_callable=mocker.AsyncMock)
    feed = mocker.patch("src.main.TickerFeed")
    realtime_exit = mocker.patch("src.main.RealtimeExit")
    config = TradingConfig(timeframe=Timeframe.MINUTE_30, markets=("KRW-BTC",))

    await run(Path("config.json"), config, DB_CONFIG)

    # 현재가는 실시간 손절·익절로 가고, 판단 작업은 애플리케이션의 마켓 잠금을 거친다
    assert feed.call_args.kwargs == {"markets": ("KRW-BTC",), "on_ticker": realtime_exit.return_value.on_ticker}
    exit_kwargs = realtime_exit.call_args.kwargs
    assert exit_kwargs["market_ids"] == {"KRW-BTC": 1}
    assert exit_kwargs["submit"].__func__ is Application.submit


async def test_run_closes_database_when_market_registration_fails(mocker: MockerFixture) -> None:
    events: list[str] = []
    _record(mocker, "src.main.Database.connect", events, "db open")
    _record(mocker, "src.main.Database.close", events, "db close")
    mocker.patch("src.main.register_markets", new_callable=mocker.AsyncMock, side_effect=OSError("connection lost"))
    _record(mocker, "src.main.Application.run", events, "run")

    with pytest.raises(OSError, match="connection lost"):
        await run(Path("config.json"), TradingConfig(timeframe=Timeframe.MINUTE_30, markets=("KRW-BTC",)), DB_CONFIG)

    assert events == ["db open", "db close"]


@pytest.mark.integration
async def test_register_markets_is_idempotent(database: Database) -> None:
    symbols = ["KRW-T" + uuid.uuid4().hex[:8].upper() for _ in range(2)]
    try:
        first = await register_markets(database, symbols)
        again = await register_markets(database, symbols)

        assert set(first) == set(symbols)
        assert again == first
    finally:
        async with database.transaction() as conn:
            await conn.execute("DELETE FROM market WHERE symbol = ANY($1::text[])", symbols)


CREDENTIALS = UpbitCredentials(access_key="access-value", secret_key="secret-value-" + "x" * 64)
LIVE = TradingConfig(timeframe=Timeframe.MINUTE_30, markets=("KRW-BTC", "KRW-ETH"), live=True)


def _chance(market: str, state: str = "active") -> OrderChance:
    return OrderChance(
        market=market,
        bid_fee=Decimal("0.0005"),
        ask_fee=Decimal("0.0005"),
        bid_min_total=Decimal("5000"),
        ask_min_total=Decimal("5000"),
        max_total=Decimal("1000000000"),
        state=state,
    )


def _stub_startup(mocker: MockerFixture) -> dict[str, object]:
    mocker.patch("src.main.Database.connect", new_callable=mocker.AsyncMock)
    mocker.patch("src.main.Database.close", new_callable=mocker.AsyncMock)
    mocker.patch("src.main.register_markets", new_callable=mocker.AsyncMock, return_value={"KRW-BTC": 1, "KRW-ETH": 2})
    account = Account(
        currency="KRW", balance=Decimal("10000"), locked=Decimal(0), avg_buy_price=Decimal(0), unit_currency="KRW"
    )
    return {
        "run": mocker.patch("src.main.Application.run", new_callable=mocker.AsyncMock),
        "accounts": mocker.patch(
            "src.main.UpbitClient.get_accounts", new_callable=mocker.AsyncMock, return_value=[account]
        ),
        "chance": mocker.patch(
            "src.main.UpbitClient.get_order_chance", new_callable=mocker.AsyncMock, side_effect=_chance
        ),
    }


async def test_live_run_checks_account_and_markets_before_starting(mocker: MockerFixture) -> None:
    stubs = _stub_startup(mocker)

    await run(Path("config.json"), LIVE, DB_CONFIG, CREDENTIALS)

    stubs["accounts"].assert_awaited_once()  # type: ignore[attr-defined]
    assert [call.args[0] for call in stubs["chance"].await_args_list] == ["KRW-BTC", "KRW-ETH"]  # type: ignore[attr-defined]
    stubs["run"].assert_awaited_once()  # type: ignore[attr-defined]


async def test_paper_run_does_not_touch_account(mocker: MockerFixture) -> None:
    stubs = _stub_startup(mocker)

    await run(Path("config.json"), TradingConfig(timeframe=Timeframe.MINUTE_30, markets=("KRW-BTC",)), DB_CONFIG)

    stubs["accounts"].assert_not_awaited()  # type: ignore[attr-defined]
    stubs["run"].assert_awaited_once()  # type: ignore[attr-defined]


async def test_live_run_without_credentials_does_not_start(mocker: MockerFixture) -> None:
    stubs = _stub_startup(mocker)

    with pytest.raises(StartupError, match="credentials"):
        await run(Path("config.json"), LIVE, DB_CONFIG)

    stubs["run"].assert_not_awaited()  # type: ignore[attr-defined]


async def test_live_run_stops_when_account_check_fails(mocker: MockerFixture) -> None:
    stubs = _stub_startup(mocker)
    stubs["accounts"].side_effect = UpbitAuthError(  # type: ignore[attr-defined]
        "GET /v1/accounts failed (401 no_authorization_ip): ", status=401, name="no_authorization_ip"
    )

    with pytest.raises(StartupError, match="no_authorization_ip") as error:
        await run(Path("config.json"), LIVE, DB_CONFIG, CREDENTIALS)

    stubs["run"].assert_not_awaited()  # type: ignore[attr-defined]
    assert CREDENTIALS.secret_key not in str(error.value)


async def test_live_run_stops_when_market_not_tradable(mocker: MockerFixture) -> None:
    stubs = _stub_startup(mocker)
    stubs["chance"].side_effect = lambda market: _chance(market, "delisted" if market == "KRW-ETH" else "active")  # type: ignore[attr-defined]

    with pytest.raises(StartupError, match="KRW-ETH"):
        await run(Path("config.json"), LIVE, DB_CONFIG, CREDENTIALS)

    stubs["run"].assert_not_awaited()  # type: ignore[attr-defined]


def _live_main_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "config.json"
    config.write_text('{"timeframe": "30m", "markets": ["KRW-BTC"], "live": true}', encoding="utf-8")
    for key, value in DB_ENV.items():
        monkeypatch.setenv(key, value)
    return config


def test_main_live_without_keys_fails_before_connecting(
    tmp_path: Path, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _live_main_env(tmp_path, monkeypatch)
    monkeypatch.delenv("UPBIT_ACCESS_KEY", raising=False)
    monkeypatch.delenv("UPBIT_SECRET_KEY", raising=False)
    connect = mocker.patch("src.main.Database.connect")

    assert main(["--config", str(config)]) == EXIT_STARTUP_FAILED
    connect.assert_not_called()


def test_main_live_check_failure_exits_with_startup_failed(
    tmp_path: Path, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _live_main_env(tmp_path, monkeypatch)
    monkeypatch.setenv("UPBIT_ACCESS_KEY", CREDENTIALS.access_key)
    monkeypatch.setenv("UPBIT_SECRET_KEY", CREDENTIALS.secret_key)
    stubs = _stub_startup(mocker)
    stubs["accounts"].side_effect = UpbitAuthError("401 invalid_access_key", status=401, name="invalid_access_key")  # type: ignore[attr-defined]

    assert main(["--config", str(config)]) == EXIT_STARTUP_FAILED
    stubs["run"].assert_not_awaited()  # type: ignore[attr-defined]
