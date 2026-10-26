from datetime import datetime
from decimal import Decimal

import pytest

from src.models.position import Position
from src.utils.constants import SignalDirection

SAMPLE_DICT = {
    "market": "KRW-BTC",
    "direction": "long",
    "entry_price": "140000000",
    "current_price": "142000000",
    "volume": "0.01",
    "stop_loss": "138000000",
    "take_profit": "146000000",
    "strategy_id": "ma_crossover_v1",
    "opened_at": "2025-01-02T04:28:05",
}


# ---------------------------------------------------------------------------
# from_dict — 기본 필드
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_from_dict_string_fields():
    """market, strategy_id 필드가 올바르게 매핑된다."""
    pos = Position.from_dict(SAMPLE_DICT)
    assert pos.market == "KRW-BTC"
    assert pos.strategy_id == "ma_crossover_v1"


# ---------------------------------------------------------------------------
# enum 변환
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_direction_is_signal_direction_enum():
    """문자열 direction이 SignalDirection enum으로 변환된다."""
    pos = Position.from_dict(SAMPLE_DICT)
    assert isinstance(pos.direction, SignalDirection)
    assert pos.direction == SignalDirection.LONG


@pytest.mark.unit
def test_direction_short_parsed():
    """'short' 문자열이 SignalDirection.SHORT로 변환된다."""
    pos = Position.from_dict({**SAMPLE_DICT, "direction": "short"})
    assert pos.direction == SignalDirection.SHORT


@pytest.mark.unit
def test_post_init_skips_already_enum():
    """이미 SignalDirection enum인 direction은 그대로 유지된다."""
    pos = Position.from_dict({**SAMPLE_DICT, "direction": SignalDirection.SHORT})
    assert pos.direction == SignalDirection.SHORT


@pytest.mark.unit
def test_invalid_direction_raises_value_error():
    """정의되지 않은 direction 값은 ValueError를 발생시킨다."""
    with pytest.raises(ValueError):
        Position.from_dict({**SAMPLE_DICT, "direction": "buy"})


# ---------------------------------------------------------------------------
# Decimal 변환
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_decimal_fields_are_decimal_type():
    """가격/수량 필드들이 모두 Decimal 타입으로 변환된다."""
    pos = Position.from_dict(SAMPLE_DICT)
    for field in ["entry_price", "current_price", "volume", "stop_loss", "take_profit"]:
        assert isinstance(getattr(pos, field), Decimal), f"{field} should be Decimal"


@pytest.mark.unit
def test_decimal_values():
    """가격/수량 필드들이 올바른 값을 가진다."""
    pos = Position.from_dict(SAMPLE_DICT)
    assert pos.entry_price == Decimal("140000000")
    assert pos.current_price == Decimal("142000000")
    assert pos.volume == Decimal("0.01")
    assert pos.stop_loss == Decimal("138000000")
    assert pos.take_profit == Decimal("146000000")


@pytest.mark.unit
def test_decimal_converted_from_int():
    """int로 전달된 Decimal 필드가 Decimal로 변환된다."""
    pos = Position.from_dict({**SAMPLE_DICT, "entry_price": 140000000})
    assert isinstance(pos.entry_price, Decimal)
    assert pos.entry_price == Decimal("140000000")


@pytest.mark.unit
def test_decimal_converted_from_float():
    """float로 전달된 Decimal 필드가 Decimal로 변환된다."""
    pos = Position.from_dict({**SAMPLE_DICT, "volume": 0.01})
    assert isinstance(pos.volume, Decimal)


# ---------------------------------------------------------------------------
# datetime 변환
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_opened_at_is_datetime_type():
    """문자열 opened_at이 datetime 타입으로 변환된다."""
    pos = Position.from_dict(SAMPLE_DICT)
    assert isinstance(pos.opened_at, datetime)


@pytest.mark.unit
def test_opened_at_value():
    """opened_at이 ISO 8601 문자열과 동일한 값을 가진다."""
    pos = Position.from_dict(SAMPLE_DICT)
    assert pos.opened_at == datetime(2025, 1, 2, 4, 28, 5)


@pytest.mark.unit
def test_opened_at_defaults_to_now_when_absent():
    """opened_at이 dict에 없으면 현재 시각으로 설정된다."""
    before = datetime.now()
    data = {k: v for k, v in SAMPLE_DICT.items() if k != "opened_at"}
    pos = Position.from_dict(data)
    after = datetime.now()
    assert before <= pos.opened_at <= after


# ---------------------------------------------------------------------------
# property — unrealized_pnl
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_unrealized_pnl_long_profit():
    """롱 포지션에서 현재가 > 진입가이면 양수 손익이 반환된다."""
    pos = Position.from_dict(SAMPLE_DICT)  # entry=140000000, current=142000000, vol=0.01
    expected = (Decimal("142000000") - Decimal("140000000")) * Decimal("0.01")
    assert pos.unrealized_pnl == expected


@pytest.mark.unit
def test_unrealized_pnl_long_loss():
    """롱 포지션에서 현재가 < 진입가이면 음수 손익이 반환된다."""
    pos = Position.from_dict({**SAMPLE_DICT, "current_price": "138000000"})
    assert pos.unrealized_pnl < Decimal("0")


@pytest.mark.unit
def test_unrealized_pnl_short_profit():
    """숏 포지션에서 현재가 < 진입가이면 양수 손익이 반환된다."""
    pos = Position.from_dict({**SAMPLE_DICT, "direction": "short", "current_price": "138000000"})
    expected = (Decimal("140000000") - Decimal("138000000")) * Decimal("0.01")
    assert pos.unrealized_pnl == expected


@pytest.mark.unit
def test_unrealized_pnl_short_loss():
    """숏 포지션에서 현재가 > 진입가이면 음수 손익이 반환된다."""
    pos = Position.from_dict({**SAMPLE_DICT, "direction": "short"})
    assert pos.unrealized_pnl < Decimal("0")


# ---------------------------------------------------------------------------
# property — unrealized_pnl_percent
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_unrealized_pnl_percent_long():
    """롱 포지션의 손익률이 올바르게 계산된다."""
    pos = Position.from_dict(SAMPLE_DICT)  # (142-140)/140
    expected = float((Decimal("142000000") - Decimal("140000000")) / Decimal("140000000"))
    assert pos.unrealized_pnl_percent == pytest.approx(expected)


@pytest.mark.unit
def test_unrealized_pnl_percent_zero_entry_price():
    """진입가가 0이면 손익률은 0.0을 반환한다."""
    pos = Position.from_dict({**SAMPLE_DICT, "entry_price": "0"})
    assert pos.unrealized_pnl_percent == 0.0


@pytest.mark.unit
def test_unrealized_pnl_percent_returns_float():
    """unrealized_pnl_percent는 float 타입을 반환한다."""
    pos = Position.from_dict(SAMPLE_DICT)
    assert isinstance(pos.unrealized_pnl_percent, float)


# ---------------------------------------------------------------------------
# property — value
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_value_is_current_price_times_volume():
    """포지션 평가금액이 현재가 X 수량으로 계산된다."""
    pos = Position.from_dict(SAMPLE_DICT)
    expected = Decimal("142000000") * Decimal("0.01")
    assert pos.value == expected


@pytest.mark.unit
def test_value_is_decimal_type():
    """포지션 평가금액은 Decimal 타입으로 반환된다."""
    pos = Position.from_dict(SAMPLE_DICT)
    assert isinstance(pos.value, Decimal)


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_to_dict_has_all_keys():
    """to_dict()가 모든 필드 키를 포함한다."""
    result = Position.from_dict(SAMPLE_DICT).to_dict()
    expected_keys = {
        "market",
        "direction",
        "entry_price",
        "current_price",
        "volume",
        "stop_loss",
        "take_profit",
        "strategy_id",
        "opened_at",
    }
    assert result.keys() == expected_keys


@pytest.mark.unit
def test_to_dict_direction_is_enum():
    """to_dict()의 direction은 Enum .value 문자열로 반환된다."""
    result = Position.from_dict(SAMPLE_DICT).to_dict()
    assert result["direction"] == SignalDirection.LONG.value


@pytest.mark.unit
def test_to_dict_decimal_fields_are_decimal():
    """to_dict()의 Decimal 필드는 Decimal 타입으로 반환된다."""
    result = Position.from_dict(SAMPLE_DICT).to_dict()
    for field in ["entry_price", "current_price", "volume", "stop_loss", "take_profit"]:
        assert isinstance(result[field], Decimal), f"{field} should be Decimal"


@pytest.mark.unit
def test_to_dict_opened_at_is_datetime():
    """to_dict()의 opened_at은 ISO 8601 문자열로 반환된다."""
    result = Position.from_dict(SAMPLE_DICT).to_dict()
    assert isinstance(result["opened_at"], str)


@pytest.mark.unit
def test_to_dict_string_fields():
    """to_dict()의 문자열 필드가 원본 값과 일치한다."""
    result = Position.from_dict(SAMPLE_DICT).to_dict()
    assert result["market"] == "KRW-BTC"
    assert result["strategy_id"] == "ma_crossover_v1"
