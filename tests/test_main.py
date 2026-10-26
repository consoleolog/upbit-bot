from datetime import UTC, datetime
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from src.application import Application
from src.main import EXIT_OK, EXIT_STARTUP_FAILED, candle_close_job, main, parse_args


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


async def test_candle_close_job_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    close = datetime(2026, 1, 1, 0, 30, tzinfo=UTC)

    await candle_close_job("KRW-BTC", close)()

    assert "candle close job (market=KRW-BTC" in caplog.text
