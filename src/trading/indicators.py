"""보조지표 계산. TA-Lib을 쓰는 유일한 모듈이다. 매매 로직과 정의가 다른 경계 사례는 여기서 맞춘다.

입력은 마감된 캔들의 값, 출력은 입력과 같은 길이의 값 목록이며 아직 계산되지 않은 자리는 None이다.
지표 값은 근사값이라 float로 둔다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import talib
from numpy.lib.stride_tricks import sliding_window_view

from src.exchange.candle import Candle

Series = tuple[float | None, ...]


@dataclass(frozen=True, kw_only=True)
class Line:
    """메인선과 시그널선."""

    main: Series
    signal: Series


@dataclass(frozen=True, kw_only=True)
class IndicatorSettings:
    """지표 기간. 기본값은 매매 로직 2.1의 설정 예시다."""

    ema_short: int = 5
    ema_mid: int = 8
    ema_long: int = 13
    macd_signal: int = 9
    rsi_period: int = 14
    rsi_signal: int = 9
    stochastic_k_len: int = 14
    stochastic_k_smooth: int = 3
    stochastic_d_smooth: int = 4

    def __post_init__(self) -> None:
        # 잘못된 기간을 받으면 캔들 마감 때마다 계산이 실패하므로, 만들 때 바로 막는다
        periods = vars(self)
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in periods.values()):
            raise ValueError(f"indicator periods must be positive integers: {periods}")
        if not self.ema_short < self.ema_mid < self.ema_long:
            raise ValueError(
                f"ema periods must satisfy short < mid < long: {self.ema_short}, {self.ema_mid}, {self.ema_long}"
            )


@dataclass(frozen=True, kw_only=True)
class Indicators:
    """한 마켓의 보조지표. 각 값 목록은 계산에 쓴 캔들과 같은 길이다."""

    macd_short: Line
    macd_mid: Line
    macd_long: Line
    rsi: Line
    stochastic: Line


def calculate(candles: Sequence[Candle], settings: IndicatorSettings) -> Indicators:
    """마감된 캔들로 매매 로직 3장의 보조지표를 모두 계산한다."""
    closes = [candle.close for candle in candles]
    s = settings
    return Indicators(
        macd_short=macd(closes, s.ema_short, s.ema_mid, s.macd_signal),
        macd_mid=macd(closes, s.ema_short, s.ema_long, s.macd_signal),
        macd_long=macd(closes, s.ema_mid, s.ema_long, s.macd_signal),
        rsi=rsi(closes, s.rsi_period, s.rsi_signal),
        stochastic=stochastic_slow(
            [candle.high for candle in candles],
            [candle.low for candle in candles],
            closes,
            s.stochastic_k_len,
            s.stochastic_k_smooth,
            s.stochastic_d_smooth,
        ),
    )


def macd(closes: Sequence[Decimal], fast: int, slow: int, signal: int) -> Line:
    """MACD = EMA(fast) - EMA(slow), 시그널 = MACD의 EMA(signal) (매매 로직 3.1).

    EMA의 첫 값은 처음 기간만큼의 단순평균이다 (TA-Lib 기본 동작과 같음).
    """
    if not 1 <= fast < slow or signal < 1:
        raise ValueError(f"periods must satisfy 1 <= fast < slow and signal >= 1: {fast}, {slow}, {signal}")
    values = _floats(closes)
    main = talib.EMA(values, fast) - talib.EMA(values, slow)
    return Line(main=_series(main), signal=_series(talib.EMA(main, signal)))


def rsi(closes: Sequence[Decimal], period: int, signal: int) -> Line:
    """Wilder 방식 RSI와 시그널 = RSI의 EMA(signal) (매매 로직 3.2).

    가격 변동이 전혀 없으면(상승·하락 평균이 모두 0) 50이다. TA-Lib은 이때 0을 준다.
    Wilder 평균은 한 번 변동이 생기면 0으로 돌아가지 않으므로, 변동이 없는 자리는 처음부터 종가가 모두 같은 자리뿐이다.
    """
    if period < 1 or signal < 1:
        raise ValueError(f"periods must be at least 1: {period}, {signal}")
    values = _floats(closes)
    main = talib.RSI(values, period)
    flat = np.cumsum(values != values[0]) == 0 if len(values) else np.array([], dtype=bool)
    main[flat & ~np.isnan(main)] = 50.0
    return Line(main=_series(main), signal=_series(talib.EMA(main, signal)))


def stochastic_slow(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    k_len: int,
    k_smooth: int,
    d_smooth: int,
) -> Line:
    """slow 스토캐스틱. 메인선은 slow %K = SMA(fast %K, k_smooth), 시그널은 slow %D = SMA(slow %K, d_smooth) (매매 로직 3.3).

    최근 k_len개의 최고가 = 최저가이면 그 캔들의 fast %K는 없고(None), 그 캔들이 들어간 평균도 없다.
    TA-Lib은 이때 0을 주고, TA-Lib의 SMA는 값이 하나 없으면 그 뒤가 모두 없어지므로 numpy로 계산한다.
    """
    if k_len < 1 or k_smooth < 1 or d_smooth < 1:
        raise ValueError(f"periods must be at least 1: {k_len}, {k_smooth}, {d_smooth}")
    if not len(highs) == len(lows) == len(closes):
        raise ValueError("highs, lows and closes must have the same length")
    high, low, close = _floats(highs), _floats(lows), _floats(closes)
    fast_k = np.full(len(close), np.nan)
    if len(close) >= k_len:
        highest = sliding_window_view(high, k_len).max(axis=1)
        lowest = sliding_window_view(low, k_len).min(axis=1)
        span = highest - lowest
        with np.errstate(divide="ignore", invalid="ignore"):
            fast_k[k_len - 1 :] = np.where(span > 0, (close[k_len - 1 :] - lowest) / span * 100, np.nan)
    slow_k = _sma(fast_k, k_smooth)
    return Line(main=_series(slow_k), signal=_series(_sma(slow_k, d_smooth)))


def _sma(values: np.ndarray, period: int) -> np.ndarray:
    """단순이동평균. 구간에 값이 없는(NaN) 자리가 있으면 그 구간의 평균도 없다."""
    out = np.full(len(values), np.nan)
    if len(values) >= period:
        out[period - 1 :] = sliding_window_view(values, period).mean(axis=1)
    return out


def _floats(values: Sequence[Decimal]) -> np.ndarray:
    return np.array([float(value) for value in values], dtype=float)


def _series(values: np.ndarray) -> Series:
    return tuple(None if np.isnan(value) else float(value) for value in values)
