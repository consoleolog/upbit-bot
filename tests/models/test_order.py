from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from src.models.order import Order
from src.utils.constants import OrderSide, OrderState, OrderType, SmpType, TimeInForce

SAMPLE_DICT = {
    "market": "KRW-BTC",
    "uuid": "cdd92199-2897-4e14-9448-f923320408ad",
    "side": "ask",
    "ord_type": "limit",
    "price": "140000000",
    "state": "wait",
    "created_at": "2025-07-04T15:00:00+09:00",
    "volume": "1.0",
    "remaining_volume": "1.0",
    "reserved_fee": "70000.0",
    "remaining_fee": "70000.0",
    "paid_fee": "0.0",
    "locked": "0.0",
    "executed_volume": "0.0",
    "prevented_volume": "0",
    "prevented_locked": "0",
    "trades_count": 0,
}

REQUIRED_DECIMAL_FIELDS = ["remaining_volume", "executed_volume", "reserved_fee", "remaining_fee", "paid_fee", "locked"]

OPTIONAL_DECIMAL_FIELDS = ["price", "volume", "prevented_volume", "prevented_locked"]

KST = timezone(timedelta(hours=9))


# ---------------------------------------------------------------------------
# from_dict — 기본 필드
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_from_dict_uuid_is_uuid_type():
    """문자열 uuid가 UUID 타입으로 변환된다."""
    order = Order.from_dict(SAMPLE_DICT)
    assert isinstance(order.uuid, UUID)


@pytest.mark.unit
def test_from_dict_uuid_value():
    """uuid가 샘플 문자열과 동일한 UUID 값을 가진다."""
    order = Order.from_dict(SAMPLE_DICT)
    assert order.uuid == UUID("cdd92199-2897-4e14-9448-f923320408ad")


@pytest.mark.unit
def test_post_init_skips_already_uuid():
    """이미 UUID인 필드는 그대로 유지된다."""
    uid = UUID("cdd92199-2897-4e14-9448-f923320408ad")
    order = Order.from_dict({**SAMPLE_DICT, "uuid": uid})
    assert order.uuid == uid


@pytest.mark.unit
def test_invalid_uuid_raises_value_error():
    """유효하지 않은 uuid 문자열은 ValueError를 발생시킨다."""
    with pytest.raises(ValueError):
        Order.from_dict({**SAMPLE_DICT, "uuid": "not-a-valid-uuid"})


@pytest.mark.unit
def test_from_dict_market_field():
    """market 필드가 올바르게 매핑된다."""
    order = Order.from_dict(SAMPLE_DICT)
    assert order.market == "KRW-BTC"


@pytest.mark.unit
def test_from_dict_trades_count():
    """trades_count 필드가 정수로 매핑된다."""
    order = Order.from_dict(SAMPLE_DICT)
    assert order.trades_count == 0


@pytest.mark.unit
def test_from_dict_optional_fields_absent_are_none():
    """샘플에 없는 optional 필드(identifier, time_in_force, smp_type)는 None이다."""
    order = Order.from_dict(SAMPLE_DICT)
    assert order.identifier is None
    assert order.time_in_force is None
    assert order.smp_type is None


# ---------------------------------------------------------------------------
# Decimal 변환
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_required_decimal_fields_are_decimal_type():
    """필수 Decimal 필드들이 모두 Decimal 타입으로 변환된다."""
    order = Order.from_dict(SAMPLE_DICT)
    for field in REQUIRED_DECIMAL_FIELDS:
        assert isinstance(getattr(order, field), Decimal), f"{field} should be Decimal"


@pytest.mark.unit
def test_optional_decimal_fields_are_decimal_type():
    """optional Decimal 필드들이 값이 있을 때 Decimal 타입으로 변환된다."""
    order = Order.from_dict(SAMPLE_DICT)
    for field in OPTIONAL_DECIMAL_FIELDS:
        assert isinstance(getattr(order, field), Decimal), f"{field} should be Decimal"


@pytest.mark.unit
def test_price_decimal_value():
    """price가 '140000000' 문자열에서 Decimal로 정확히 변환된다."""
    order = Order.from_dict(SAMPLE_DICT)
    assert order.price == Decimal("140000000")


@pytest.mark.unit
def test_zero_decimal_values():
    """0 값을 가진 Decimal 필드들이 Decimal("0")으로 변환된다."""
    order = Order.from_dict(SAMPLE_DICT)
    assert order.paid_fee == Decimal("0.0")
    assert order.executed_volume == Decimal("0.0")
    assert order.prevented_volume == Decimal("0")


@pytest.mark.unit
def test_optional_decimal_field_none_when_absent():
    """optional Decimal 필드가 dict에 없으면 None을 유지한다."""
    data = {**SAMPLE_DICT, "price": None}
    order = Order.from_dict(data)
    assert order.price is None


@pytest.mark.unit
def test_decimal_converted_from_int():
    """int로 전달된 Decimal 필드가 Decimal로 변환된다."""
    data = {**SAMPLE_DICT, "reserved_fee": 70000}
    order = Order.from_dict(data)
    assert isinstance(order.reserved_fee, Decimal)
    assert order.reserved_fee == Decimal("70000")


@pytest.mark.unit
def test_post_init_skips_already_decimal():
    """이미 Decimal인 필드는 그대로 유지된다."""
    data = {**SAMPLE_DICT, "price": Decimal("140000000")}
    order = Order.from_dict(data)
    assert order.price == Decimal("140000000")


# ---------------------------------------------------------------------------
# datetime 변환
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_created_at_is_datetime_type():
    """ISO 8601 문자열 created_at이 datetime 타입으로 변환된다."""
    order = Order.from_dict(SAMPLE_DICT)
    assert isinstance(order.created_at, datetime)


@pytest.mark.unit
def test_created_at_value():
    """created_at이 올바른 날짜/시각 값을 가진다."""
    order = Order.from_dict(SAMPLE_DICT)
    assert order.created_at.year == 2025
    assert order.created_at.month == 7
    assert order.created_at.day == 4
    assert order.created_at.hour == 15
    assert order.created_at.minute == 0
    assert order.created_at.second == 0


@pytest.mark.unit
def test_created_at_timezone_aware():
    """created_at이 타임존 정보(+09:00)를 포함한 timezone-aware datetime이다."""
    order = Order.from_dict(SAMPLE_DICT)
    assert order.created_at.tzinfo is not None
    assert order.created_at.utcoffset() == timedelta(hours=9)


@pytest.mark.unit
def test_post_init_skips_already_datetime():
    """이미 datetime인 created_at은 그대로 유지된다."""
    dt = datetime(2025, 7, 4, 15, 0, 0, tzinfo=KST)
    data = {**SAMPLE_DICT, "created_at": dt}
    order = Order.from_dict(data)
    assert order.created_at == dt


# ---------------------------------------------------------------------------
# enum 변환
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_side_is_order_side_enum():
    """문자열 side가 OrderSide enum으로 변환된다."""
    order = Order.from_dict(SAMPLE_DICT)
    assert isinstance(order.side, OrderSide)
    assert order.side == OrderSide.ASK


@pytest.mark.unit
def test_ord_type_is_order_type_enum():
    """문자열 ord_type이 OrderType enum으로 변환된다."""
    order = Order.from_dict(SAMPLE_DICT)
    assert isinstance(order.ord_type, OrderType)
    assert order.ord_type == OrderType.LIMIT


@pytest.mark.unit
def test_state_is_order_state_enum():
    """문자열 state가 OrderState enum으로 변환된다."""
    order = Order.from_dict(SAMPLE_DICT)
    assert isinstance(order.state, OrderState)
    assert order.state == OrderState.WAIT


@pytest.mark.unit
def test_side_bid_parsed():
    """'bid' 문자열이 OrderSide.BID로 변환된다."""
    data = {**SAMPLE_DICT, "side": "bid"}
    order = Order.from_dict(data)
    assert order.side == OrderSide.BID


@pytest.mark.unit
def test_time_in_force_parsed_when_present():
    """time_in_force 문자열이 TimeInForce enum으로 변환된다."""
    for value, expected in [("ioc", TimeInForce.IOC), ("fok", TimeInForce.FOK), ("post_only", TimeInForce.POST_ONLY)]:
        order = Order.from_dict({**SAMPLE_DICT, "time_in_force": value})
        assert order.time_in_force == expected


@pytest.mark.unit
def test_smp_type_parsed_when_present():
    """smp_type 문자열이 SmpType enum으로 변환된다."""
    for value, expected in [
        ("cancel_maker", SmpType.CANCEL_MAKER),
        ("cancel_taker", SmpType.CANCEL_TAKER),
        ("reduce", SmpType.REDUCE),
    ]:
        order = Order.from_dict({**SAMPLE_DICT, "smp_type": value})
        assert order.smp_type == expected


@pytest.mark.unit
def test_post_init_skips_already_enum():
    """이미 enum인 필드는 그대로 유지된다."""
    data = {**SAMPLE_DICT, "side": OrderSide.BID, "state": OrderState.DONE}
    order = Order.from_dict(data)
    assert order.side == OrderSide.BID
    assert order.state == OrderState.DONE


@pytest.mark.unit
def test_invalid_side_raises_value_error():
    """정의되지 않은 side 값은 ValueError를 발생시킨다."""
    with pytest.raises(ValueError):
        Order.from_dict({**SAMPLE_DICT, "side": "buy"})


@pytest.mark.unit
def test_invalid_ord_type_raises_value_error():
    """정의되지 않은 ord_type 값은 ValueError를 발생시킨다."""
    with pytest.raises(ValueError):
        Order.from_dict({**SAMPLE_DICT, "ord_type": "instant"})


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_to_dict_has_all_keys():
    """to_dict()가 모든 필드 키를 포함한다."""
    result = Order.from_dict(SAMPLE_DICT).to_dict()
    expected_keys = {
        "uuid",
        "side",
        "ord_type",
        "price",
        "state",
        "market",
        "created_at",
        "volume",
        "remaining_volume",
        "executed_volume",
        "trades_count",
        "reserved_fee",
        "remaining_fee",
        "paid_fee",
        "locked",
        "identifier",
        "time_in_force",
        "smp_type",
        "prevented_volume",
        "prevented_locked",
    }
    assert result.keys() == expected_keys


@pytest.mark.unit
def test_to_dict_enum_fields_are_enum_type():
    """to_dict()의 enum 필드는 Enum .value 문자열로 반환된다."""
    result = Order.from_dict(SAMPLE_DICT).to_dict()
    assert result["side"] == OrderSide.ASK.value
    assert result["ord_type"] == OrderType.LIMIT.value
    assert result["state"] == OrderState.WAIT.value


@pytest.mark.unit
def test_to_dict_created_at_is_datetime():
    """to_dict()의 created_at은 ISO 8601 문자열로 반환된다."""
    result = Order.from_dict(SAMPLE_DICT).to_dict()
    assert isinstance(result["created_at"], str)


@pytest.mark.unit
def test_to_dict_decimal_fields_are_decimal():
    """to_dict()의 Decimal 필드는 Decimal 타입으로 반환된다."""
    result = Order.from_dict(SAMPLE_DICT).to_dict()
    for field in REQUIRED_DECIMAL_FIELDS + OPTIONAL_DECIMAL_FIELDS:
        value = result[field]
        if value is not None:
            assert isinstance(value, Decimal), f"{field} should be Decimal"


@pytest.mark.unit
def test_to_dict_uuid_is_uuid_type():
    """to_dict()의 uuid는 UUID 타입으로 반환된다."""
    result = Order.from_dict(SAMPLE_DICT).to_dict()
    assert isinstance(result["uuid"], UUID)


@pytest.mark.unit
def test_to_dict_uuid_and_market():
    """to_dict()의 uuid, market 값이 원본과 일치한다."""
    result = Order.from_dict(SAMPLE_DICT).to_dict()
    assert result["uuid"] == UUID(SAMPLE_DICT["uuid"])
    assert result["market"] == SAMPLE_DICT["market"]
