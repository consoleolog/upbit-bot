from datetime import UTC, datetime

import pytest

from src.repositories.models import IndicatorInfo, IndicatorType

T0 = datetime(2026, 1, 1, tzinfo=UTC)
NAIVE = datetime(2026, 1, 1)


def _info(**overrides: object) -> IndicatorInfo:
    values: dict[str, object] = {
        "market_id": 1,
        "indicator_type": IndicatorType.RSI,
        "value": 50.0,
        "signal": None,
        "candle_time": T0,
    }
    return IndicatorInfo(**(values | overrides))  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["candle_time", "over_time", "cross_time"])
def test_naive_time_rejected(field: str) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _info(**{field: NAIVE})


@pytest.mark.parametrize("field", ["value", "signal"])
@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_value_rejected(field: str, number: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        _info(**{field: number})


def test_valid_info() -> None:
    info = _info(value=None, signal=-0.000123)

    assert info.value is None and info.signal == -0.000123
