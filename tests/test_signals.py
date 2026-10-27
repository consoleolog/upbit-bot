import pytest

from src.indicators import Line
from src.signals import CrossState, OverState, Threshold, cross, over

RSI = Threshold(over_sold=31, over_bought=60)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        # 기준과 같은 값도 과매도/과매수다
        ((31.0,), OverState.OVER_SOLD),
        ((20.0,), OverState.OVER_SOLD),
        ((60.0,), OverState.OVER_BOUGHT),
        ((31.1,), None),
        ((59.9,), None),
        # 스토캐스틱은 %K와 %D가 모두 기준을 넘어야 한다
        ((20.0, 30.0), OverState.OVER_SOLD),
        ((20.0, 40.0), None),
        ((70.0, 61.0), OverState.OVER_BOUGHT),
        ((70.0, 50.0), None),
        # 값이 없으면 판정하지 않는다
        ((None,), None),
        ((20.0, None), None),
        ((), None),
    ],
)
def test_over(values: tuple[float | None, ...], expected: OverState | None) -> None:
    assert over(values, RSI) == expected


def _line(*pairs: tuple[float | None, float | None]) -> Line:
    return Line(main=tuple(m for m, _ in pairs), signal=tuple(s for _, s in pairs))


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (_line((1, 2), (3, 2)), CrossState.GOLDEN_CROSS),
        (_line((2, 2), (3, 2)), CrossState.GOLDEN_CROSS),
        (_line((3, 2), (1, 2)), CrossState.DEAD_CROSS),
        (_line((2, 2), (1, 2)), CrossState.DEAD_CROSS),
        # 두 선이 같아지거나, 교차 없이 같은 쪽에 있으면 크로스가 아니다
        (_line((1, 2), (2, 2)), None),
        (_line((3, 2), (2, 2)), None),
        (_line((3, 2), (4, 2)), None),
        (_line((1, 2), (0, 2)), None),
        # 판단은 마지막 캔들에서만 한다
        (_line((1, 2), (3, 2), (4, 2)), None),
        # 값이 없거나 캔들이 부족하면 판정하지 않는다
        (_line((None, None), (3, 2)), None),
        (_line((1, None), (3, 2)), None),
        (_line((1, 2), (3, None)), None),
        (_line((3, 2)), None),
        (_line(), None),
    ],
)
def test_cross(line: Line, expected: CrossState | None) -> None:
    assert cross(line) == expected


@pytest.mark.parametrize(
    ("over_sold", "over_bought"), [(60, 31), (50, 50), (-1, 60), (31, 101), (float("nan"), 60), (True, 60), ("31", 60)]
)
def test_invalid_threshold_rejected(over_sold: object, over_bought: object) -> None:
    with pytest.raises(ValueError, match="threshold"):
        Threshold(over_sold=over_sold, over_bought=over_bought)  # type: ignore[arg-type]
