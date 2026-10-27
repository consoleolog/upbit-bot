"""보조지표 계산. TA-Lib을 쓰는 유일한 모듈이다.

입력은 마감된 캔들의 값, 출력은 입력과 같은 길이의 값 목록이며 아직 계산되지 않은 자리는 None이다.
지표 값은 근사값이라 float로 둔다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import talib

Series = tuple[float | None, ...]


@dataclass(frozen=True, kw_only=True)
class Line:
    """메인선과 시그널선."""

    main: Series
    signal: Series


def macd(closes: Sequence[Decimal], fast: int, slow: int, signal: int) -> Line:
    """MACD = EMA(fast) - EMA(slow), 시그널 = MACD의 EMA(signal) (매매 로직 3.1).

    EMA의 첫 값은 처음 기간만큼의 단순평균이다 (TA-Lib 기본 동작과 같음).
    """
    if not 1 <= fast < slow or signal < 1:
        raise ValueError(f"periods must satisfy 1 <= fast < slow and signal >= 1: {fast}, {slow}, {signal}")
    values = _floats(closes)
    main = talib.EMA(values, fast) - talib.EMA(values, slow)
    return Line(main=_series(main), signal=_series(talib.EMA(main, signal)))


def _floats(values: Sequence[Decimal]) -> np.ndarray:
    return np.array([float(value) for value in values], dtype=float)


def _series(values: np.ndarray) -> Series:
    return tuple(None if np.isnan(value) else float(value) for value in values)
