from decimal import Decimal
from itertools import pairwise

import pytest

from src.indicators import Line, macd, rsi


def _ema(values: list[float | None], period: int) -> list[float | None]:
    """기준 구현: 평활 계수 2/(기간+1), 첫 값은 처음 기간만큼(앞쪽 None 제외)의 단순평균 (매매 로직 3.1)."""
    out: list[float | None] = [None] * len(values)
    start = next((i for i, v in enumerate(values) if v is not None), len(values))
    if start + period > len(values):
        return out
    k = 2 / (period + 1)
    prev = sum(v for v in values[start : start + period] if v is not None) / period
    out[start + period - 1] = prev
    for i in range(start + period, len(values)):
        value = values[i]
        assert value is not None
        prev = value * k + prev * (1 - k)
        out[i] = prev
    return out


def _approx(values: tuple[float | None, ...] | list[float | None]) -> list[object]:
    return [None if v is None else pytest.approx(v, rel=1e-9) for v in values]


CLOSES = [Decimal(v) for v in ("100", "102", "101", "105", "107", "106", "110", "108", "111", "115", "113", "112")]


def test_macd_matches_reference_ema() -> None:
    floats: list[float | None] = [float(c) for c in CLOSES]
    fast, slow = _ema(floats, 3), _ema(floats, 5)
    main = [None if f is None or s is None else f - s for f, s in zip(fast, slow, strict=True)]

    line = macd(CLOSES, fast=3, slow=5, signal=2)

    assert list(line.main) == _approx(main)
    assert list(line.signal) == _approx(_ema(main, 2))


def test_macd_values_start_after_warm_up() -> None:
    line = macd(CLOSES, fast=3, slow=5, signal=2)

    # MACD는 EMA(slow)가 나오는 5번째부터, 시그널은 그 뒤 2개가 모인 6번째부터 값이 있다
    assert line.main[:4] == (None,) * 4 and line.main[4] is not None
    assert line.signal[:5] == (None,) * 5 and line.signal[5] is not None
    assert len(line.main) == len(line.signal) == len(CLOSES)


def test_macd_with_too_few_candles_is_all_none() -> None:
    line = macd(CLOSES[:4], fast=3, slow=5, signal=2)

    assert line.main == (None,) * 4
    assert line.signal == (None,) * 4


def test_macd_with_no_candles() -> None:
    line = macd([], fast=3, slow=5, signal=2)

    assert line.main == ()
    assert line.signal == ()


@pytest.mark.parametrize(("fast", "slow", "signal"), [(0, 5, 2), (5, 5, 2), (8, 5, 2), (3, 5, 0)])
def test_macd_rejects_invalid_periods(fast: int, slow: int, signal: int) -> None:
    with pytest.raises(ValueError, match="periods"):
        macd(CLOSES, fast=fast, slow=slow, signal=signal)


def _wilder_rsi(closes: list[float], period: int) -> list[float | None]:
    """기준 구현: Wilder 평균. 하락이 없으면 100, 변동이 전혀 없으면 50 (매매 로직 3.2)."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    changes = [b - a for a, b in pairwise(closes)]
    gain = sum(max(c, 0) for c in changes[:period]) / period
    loss = sum(max(-c, 0) for c in changes[:period]) / period
    for i in range(period, len(closes)):
        if i > period:
            change = changes[i - 1]
            gain = (gain * (period - 1) + max(change, 0)) / period
            loss = (loss * (period - 1) + max(-change, 0)) / period
        out[i] = 50.0 if gain + loss == 0 else 100 * gain / (gain + loss)
    return out


def _decimals(values: list[float]) -> list[Decimal]:
    return [Decimal(str(v)) for v in values]


RSI_CLOSES = [100, 102, 101, 105, 107, 106, 110, 108, 111, 115, 113, 112, 116, 118, 117, 119, 121, 120]


def test_rsi_matches_reference() -> None:
    line = rsi(_decimals(RSI_CLOSES), period=5, signal=3)

    expected = _wilder_rsi([float(c) for c in RSI_CLOSES], 5)
    assert list(line.main) == _approx(expected)
    assert list(line.signal) == _approx(_ema(expected, 3))


@pytest.mark.parametrize(
    ("closes", "value"),
    [
        # 가격 변동이 전혀 없으면 50 (TA-Lib은 0)
        ([100] * 8, 50.0),
        # 상승만 있으면 100, 하락만 있으면 0
        ([100, 101, 102, 103, 104, 105, 106, 107], 100.0),
        ([107, 106, 105, 104, 103, 102, 101, 100], 0.0),
    ],
)
def test_rsi_boundary_cases(closes: list[float], value: float) -> None:
    line = rsi(_decimals(closes), period=5, signal=3)

    assert [v for v in line.main if v is not None] == [value] * 3


def test_rsi_leaves_flat_after_first_change() -> None:
    line = rsi(_decimals([100] * 7 + [101, 100]), period=5, signal=3)

    # 변동이 없던 동안은 50, 첫 상승 뒤 100, 그 뒤로는 하락이 섞여 100보다 작다
    assert line.main[5:7] == (50.0, 50.0)
    assert line.main[7] == 100.0
    assert line.main[8] is not None and 0 < line.main[8] < 100


def test_rsi_with_too_few_candles_is_all_none() -> None:
    assert rsi(_decimals([100, 101, 102]), period=5, signal=3).main == (None,) * 3
    assert rsi([], period=5, signal=3) == Line(main=(), signal=())


@pytest.mark.parametrize(("period", "signal"), [(0, 3), (5, 0)])
def test_rsi_rejects_invalid_periods(period: int, signal: int) -> None:
    with pytest.raises(ValueError, match="periods"):
        rsi(_decimals(RSI_CLOSES), period=period, signal=signal)
