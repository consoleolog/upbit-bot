from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.candle import Candle
from src.config import Timeframe
from src.indicators import IndicatorSettings, Line, calculate
from src.market_worker import MarketWorker, _latest


class FakeUpbit:
    def __init__(self, candles: list[Candle]) -> None:
        self.candles = candles
        self.calls: list[tuple[str, Timeframe, datetime]] = []

    async def get_candles(self, market: str, timeframe: Timeframe, to: datetime) -> list[Candle]:
        self.calls.append((market, timeframe, to))
        return self.candles


def _candle(start: datetime) -> Candle:
    return Candle(market="KRW-BTC", start=start, open=Decimal(1), high=Decimal(2), low=Decimal(1), close=Decimal(2))


def _worker(upbit: FakeUpbit) -> MarketWorker:
    return MarketWorker(upbit=upbit, timeframe=Timeframe.MINUTE_30, settings=IndicatorSettings())


async def test_candle_close_job_fetches_closed_candles(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    close = datetime(2026, 1, 1, 0, 30, tzinfo=UTC)
    upbit = FakeUpbit([_candle(datetime(2026, 1, 1, 0, 0, tzinfo=UTC))])

    await _worker(upbit).candle_close_job("KRW-BTC", close)()

    # 마감 시각을 to로 넘겨 마감된 캔들만 받는다
    assert upbit.calls == [("KRW-BTC", Timeframe.MINUTE_30, close)]
    assert "candles=1, last=2026-01-01T09:00:00+09:00, last_close=2" in caplog.text


async def test_candle_close_job_with_no_candles(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    close = datetime(2026, 1, 1, 0, 30, tzinfo=UTC)

    await _worker(FakeUpbit([])).candle_close_job("KRW-BTC", close)()

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

    await _worker(FakeUpbit(candles)).candle_close_job("KRW-BTC", close)()

    rsi_main = expected.rsi.main[-1]
    assert rsi_main is not None
    assert f"rsi={rsi_main:.6g}/" in caplog.text
    assert "macd_short=None" not in caplog.text


def test_latest_keeps_small_values_visible() -> None:
    line = Line(main=(None, 0.000123456), signal=(None, -0.0000654321))

    assert _latest(line) == "0.000123456/-6.54321e-05"
    assert _latest(Line(main=(None,), signal=(None,))) == "None/None"
