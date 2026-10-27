from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from src.application import Application
from src.config import Timeframe, TradingConfig
from src.main import EXIT_OK, EXIT_STARTUP_FAILED, main, parse_args, run


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


def test_main_runs_application_with_scheduler(tmp_path: Path, mocker: MockerFixture) -> None:
    config = tmp_path / "config.json"
    config.write_text('{"timeframe": "30m", "markets": ["KRW-BTC"]}', encoding="utf-8")
    run = mocker.patch("src.main.Application.run", new_callable=mocker.AsyncMock)
    add_service = mocker.spy(Application, "add_service")

    code = main(["--config", str(config)])

    assert code == EXIT_OK
    run.assert_awaited_once()
    assert add_service.call_args.args[1] == "scheduler:30m"


async def test_run_closes_client_after_application_stops(mocker: MockerFixture) -> None:
    events: list[str] = []
    mocker.patch("src.main.UpbitClient.open", side_effect=lambda: events.append("open"), new_callable=mocker.AsyncMock)
    mocker.patch(
        "src.main.UpbitClient.close", side_effect=lambda: events.append("close"), new_callable=mocker.AsyncMock
    )
    mocker.patch("src.main.Application.run", side_effect=lambda: events.append("run"), new_callable=mocker.AsyncMock)

    await run(Path("config.json"), TradingConfig(timeframe=Timeframe.MINUTE_30, markets=("KRW-BTC",)))

    assert events == ["open", "run", "close"]
