import logging
from datetime import datetime
from typing import Protocol

from src.application import Job
from src.candle import Candle
from src.config import Timeframe
from src.indicators import IndicatorSettings, Line, calculate

logger = logging.getLogger(__name__)


class CandleSource(Protocol):
    async def get_candles(self, market: str, timeframe: Timeframe, to: datetime) -> list[Candle]: ...


class MarketWorker:
    """마켓의 캔들 마감 처리. 지금은 마감된 캔들을 조회해 보조지표를 계산하고 기록만 남긴다."""

    def __init__(self, *, upbit: CandleSource, timeframe: Timeframe, settings: IndicatorSettings) -> None:
        self._upbit = upbit
        self._timeframe = timeframe
        self._settings = settings

    def candle_close_job(self, market: str, close: datetime) -> Job:
        """스케줄러에 넘기는 작업. 마켓의 close 시각 캔들 마감을 처리한다."""

        async def job() -> None:
            await self.on_candle_close(market, close)

        return job

    async def on_candle_close(self, market: str, close: datetime) -> None:
        candles = await self._upbit.get_candles(market, self._timeframe, close)
        last = candles[-1] if candles else None
        indicators = calculate(candles, self._settings)
        logger.info(
            "candle close job (market=%s, close=%s, candles=%d, last=%s, last_close=%s, "
            "rsi=%s, stochastic=%s, macd_short=%s, macd_mid=%s, macd_long=%s)",
            market,
            close.isoformat(),
            len(candles),
            last.start_kst.isoformat() if last else None,
            last.close if last else None,
            _latest(indicators.rsi),
            _latest(indicators.stochastic),
            _latest(indicators.macd_short),
            _latest(indicators.macd_mid),
            _latest(indicators.macd_long),
        )


def _latest(line: Line) -> str:
    """마지막 캔들의 메인선/시그널선 값. 값이 없으면 None으로 적는다."""
    if not line.main:
        return "None/None"
    main, signal = line.main[-1], line.signal[-1]
    # 유효숫자로 적어, 가격이 낮은 코인의 작은 MACD 값도 0으로 보이지 않게 한다
    return f"{'None' if main is None else f'{main:.6g}'}/{'None' if signal is None else f'{signal:.6g}'}"
