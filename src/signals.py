"""신호 판정 (매매 로직 4.1). 판단할 캔들은 지표 값 목록의 마지막 캔들이다."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from src.indicators import Line


class OverState(StrEnum):
    OVER_BOUGHT = "OVER_BOUGHT"
    OVER_SOLD = "OVER_SOLD"


class CrossState(StrEnum):
    GOLDEN_CROSS = "GOLDEN_CROSS"
    DEAD_CROSS = "DEAD_CROSS"


@dataclass(frozen=True, kw_only=True)
class Threshold:
    """과매도 / 과매수 기준."""

    over_sold: float
    over_bought: float

    def __post_init__(self) -> None:
        # 기준이 뒤집히면 한 값이 과매도와 과매수에 동시에 걸리므로 만들 때 막는다
        values = (self.over_sold, self.over_bought)
        if any(not isinstance(v, int | float) or isinstance(v, bool) for v in values):
            raise ValueError(f"threshold must be numbers: {self}")
        if not 0 <= self.over_sold < self.over_bought <= 100:
            raise ValueError(f"threshold must satisfy 0 <= over_sold < over_bought <= 100: {self}")


def over(values: Sequence[float | None], threshold: Threshold) -> OverState | None:
    """주어진 값(RSI는 RSI, 스토캐스틱은 %K와 %D)이 모두 기준을 넘으면 과매도/과매수. 값이 하나라도 없으면 판정하지 않는다."""
    if not values or None in values:
        return None
    if all(v <= threshold.over_sold for v in values if v is not None):
        return OverState.OVER_SOLD
    if all(v >= threshold.over_bought for v in values if v is not None):
        return OverState.OVER_BOUGHT
    return None


def cross(line: Line) -> CrossState | None:
    """직전 캔들과 마지막 캔들 사이에 메인선이 시그널선을 교차했는지. 두 선이 같으면 교차가 아니다."""
    if len(line.main) < 2:
        return None
    prev_main, prev_signal, main, signal = line.main[-2], line.signal[-2], line.main[-1], line.signal[-1]
    if prev_main is None or prev_signal is None or main is None or signal is None:
        return None
    if prev_main <= prev_signal and main > signal:
        return CrossState.GOLDEN_CROSS
    if prev_main >= prev_signal and main < signal:
        return CrossState.DEAD_CROSS
    return None
