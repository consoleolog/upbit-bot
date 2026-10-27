from decimal import Decimal

import pytest

from src.indicators import macd


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
