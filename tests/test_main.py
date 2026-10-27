from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from src.application import Application
from src.candle import Candle
from src.config import Timeframe, TradingConfig
from src.indicators import IndicatorSettings, Line, calculate
from src.main import EXIT_OK, EXIT_STARTUP_FAILED, _latest, candle_close_job_factory, main, parse_args, run


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


class FakeUpbit:
    def __init__(self, candles: list[Candle]) -> None:
        self.candles = candles
        self.calls: list[tuple[str, Timeframe, datetime]] = []

    async def get_candles(self, market: str, timeframe: Timeframe, to: datetime) -> list[Candle]:
        self.calls.append((market, timeframe, to))
        return self.candles


def _candle(start: datetime) -> Candle:
    return Candle(market="KRW-BTC", start=start, open=Decimal(1), high=Decimal(2), low=Decimal(1), close=Decimal(2))


async def test_candle_close_job_fetches_closed_candles(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    close = datetime(2026, 1, 1, 0, 30, tzinfo=UTC)
    upbit = FakeUpbit([_candle(datetime(2026, 1, 1, 0, 0, tzinfo=UTC))])

    await candle_close_job_factory(upbit, Timeframe.MINUTE_30, IndicatorSettings())("KRW-BTC", close)()

    # 마감 시각을 to로 넘겨 마감된 캔들만 받는다
    assert upbit.calls == [("KRW-BTC", Timeframe.MINUTE_30, close)]
    assert "candles=1, last=2026-01-01T09:00:00+09:00, last_close=2" in caplog.text


async def test_candle_close_job_with_no_candles(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    close = datetime(2026, 1, 1, 0, 30, tzinfo=UTC)

    await candle_close_job_factory(FakeUpbit([]), Timeframe.MINUTE_30, IndicatorSettings())("KRW-BTC", close)()

    assert "candles=0, last=None" in caplog.text
    assert "rsi=None/None, stochastic=None/None" in caplog.text


async def test_candle_close_job_logs_latest_indicators(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    close = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    # 30개 캔들: 종가가 1씩 오르다가 마지막에 꺾임. 고가·저가는 종가 ±1
    closes = [100 + i for i in range(29)] + [120]
    candles = [
        Candle(
            market="KRW-BTC",
            start=close - timedelta(minutes=30 * (len(closes) - i)),
            open=Decimal(c),
            high=Decimal(c + 1),
            low=Decimal(c - 1),
            close=Decimal(c),
        )
        for i, c in enumerate(closes)
    ]
    expected = calculate(candles, IndicatorSettings())

    await candle_close_job_factory(FakeUpbit(candles), Timeframe.MINUTE_30, IndicatorSettings())("KRW-BTC", close)()

    rsi_main = expected.rsi.main[-1]
    assert rsi_main is not None
    assert f"rsi={rsi_main:.6g}/" in caplog.text
    assert "macd_short=None" not in caplog.text


async def test_run_closes_client_after_application_stops(mocker: MockerFixture) -> None:
    events: list[str] = []
    mocker.patch("src.main.UpbitClient.open", side_effect=lambda: events.append("open"), new_callable=mocker.AsyncMock)
    mocker.patch(
        "src.main.UpbitClient.close", side_effect=lambda: events.append("close"), new_callable=mocker.AsyncMock
    )
    mocker.patch("src.main.Application.run", side_effect=lambda: events.append("run"), new_callable=mocker.AsyncMock)

    await run(Path("config.json"), TradingConfig(timeframe=Timeframe.MINUTE_30, markets=("KRW-BTC",)))

    assert events == ["open", "run", "close"]


def test_latest_keeps_small_values_visible() -> None:
    line = Line(main=(None, 0.000123456), signal=(None, -0.0000654321))

    assert _latest(line) == "0.000123456/-6.54321e-05"
    assert _latest(Line(main=(None,), signal=(None,))) == "None/None"
