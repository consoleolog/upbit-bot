"""Repository에 들어오는 값의 검사. DB 제약으로 막히지 않는 값을 기록하기 전에 막는다."""

from decimal import Decimal


def check_decimal(name: str, value: Decimal, *, positive: bool) -> None:
    """유한한 0 이상(positive면 0 초과)의 값이 아니면 ValueError."""
    # PostgreSQL numeric은 NaN과 Infinity를 받고, 둘 다 "> 0" 비교를 통과해 CHECK로는 막히지 않는다
    if not value.is_finite() or value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be a finite {'positive' if positive else 'non-negative'} number: {value!r}")
