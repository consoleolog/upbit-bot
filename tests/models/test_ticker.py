from datetime import datetime
from decimal import Decimal

import pytest

from src.models.ticker import Ticker
from src.utils.constants import AskBid, ChangeDirection, MarketState, StreamType

SAMPLE_DICT = {
    "type": "ticker",
    "code": "KRW-BTC",
    "opening_price": 31883000,
    "high_price": 32310000,
    "low_price": 31855000,
    "trade_price": 32287000,
    "prev_closing_price": 31883000.00000000,
    "acc_trade_price": 78039261076.51241000,
    "change": "RISE",
    "change_price": 404000.00000000,
    "signed_change_price": 404000.00000000,
    "change_rate": 0.0126713295,
    "signed_change_rate": 0.0126713295,
    "ask_bid": "ASK",
    "trade_volume": 0.03103806,
    "acc_trade_volume": 2429.58834336,
    "trade_date": "20230221",
    "trade_time": "074102",
    "trade_timestamp": 1676965262139,
    "acc_ask_volume": 1146.25573608,
    "acc_bid_volume": 1283.33260728,
    "highest_52_week_price": 57678000.00000000,
    "highest_52_week_date": "2022-03-28",
    "lowest_52_week_price": 20700000.00000000,
    "lowest_52_week_date": "2022-12-30",
    "market_state": "ACTIVE",
    "timestamp": 1676965262177,
    "acc_trade_price_24h": 228827082483.70729000,
    "acc_trade_volume_24h": 7158.80283560,
    "stream_type": "REALTIME",
}

DECIMAL_FIELDS = [
    "opening_price",
    "high_price",
    "low_price",
    "trade_price",
    "prev_closing_price",
    "change_price",
    "signed_change_price",
    "change_rate",
    "signed_change_rate",
    "trade_volume",
    "acc_trade_volume",
    "acc_trade_volume_24h",
    "acc_trade_price",
    "acc_trade_price_24h",
    "acc_ask_volume",
    "acc_bid_volume",
]

OPTIONAL_DECIMAL_FIELDS = ["highest_52_week_price", "lowest_52_week_price"]


# ---------------------------------------------------------------------------
# from_dict — 기본 필드
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_from_dict_string_fields():
    """type, code 필드가 올바르게 매핑된다."""
    ticker = Ticker.from_dict(SAMPLE_DICT)
    assert ticker.type == "ticker"
    assert ticker.code == "KRW-BTC"


@pytest.mark.unit
def test_from_dict_int_fields():
    """timestamp, trade_timestamp 필드가 정수로 매핑된다."""
    ticker = Ticker.from_dict(SAMPLE_DICT)
    assert ticker.timestamp == 1676965262177
    assert ticker.trade_timestamp == 1676965262139


# ---------------------------------------------------------------------------
# Decimal 변환
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_from_dict_all_decimal_fields_are_decimal_type():
    """모든 Decimal 필드가 Decimal 타입으로 변환된다."""
    ticker = Ticker.from_dict(SAMPLE_DICT)
    for field in DECIMAL_FIELDS:
        assert isinstance(getattr(ticker, field), Decimal), f"{field} should be Decimal"


@pytest.mark.unit
def test_decimal_converted_from_int():
    """int로 전달된 가격 필드가 Decimal로 변환된다."""
    ticker = Ticker.from_dict(SAMPLE_DICT)
    assert isinstance(ticker.opening_price, Decimal)
    assert ticker.opening_price == Decimal("31883000")


@pytest.mark.unit
def test_decimal_converted_from_float():
    """float로 전달된 비율 필드가 Decimal로 변환된다."""
    ticker = Ticker.from_dict(SAMPLE_DICT)
    assert isinstance(ticker.change_rate, Decimal)
    assert ticker.change_rate == Decimal(str(0.0126713295))


@pytest.mark.unit
def test_decimal_small_volume_precision():
    """소수점 거래량의 정밀도가 손실되지 않는다."""
    ticker = Ticker.from_dict(SAMPLE_DICT)
    assert ticker.trade_volume == Decimal(str(0.03103806))


# ---------------------------------------------------------------------------
# datetime 변환
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_trade_date_is_datetime_type():
    """trade_date(yyyyMMdd 포맷)가 datetime 타입으로 변환된다."""
    ticker = Ticker.from_dict(SAMPLE_DICT)
    assert isinstance(ticker.trade_date, datetime)


@pytest.mark.unit
def test_trade_date_value():
    """trade_date가 '20230221' 문자열과 동일한 날짜 값을 가진다."""
    ticker = Ticker.from_dict(SAMPLE_DICT)
    assert ticker.trade_date == datetime(2023, 2, 21)


@pytest.mark.unit
def test_trade_time_is_datetime_type():
    """trade_time(HHmmss 포맷)이 datetime 타입으로 변환된다."""
    ticker = Ticker.from_dict(SAMPLE_DICT)
    assert isinstance(ticker.trade_time, datetime)


@pytest.mark.unit
def test_trade_time_value():
    """trade_time이 '074102' 문자열과 동일한 시각 값을 가진다."""
    ticker = Ticker.from_dict(SAMPLE_DICT)
    assert ticker.trade_time.hour == 7
    assert ticker.trade_time.minute == 41
    assert ticker.trade_time.second == 2


@pytest.mark.unit
def test_highest_52_week_date_is_datetime_type():
    """highest_52_week_date(yyyy-MM-dd 포맷)가 datetime 타입으로 변환된다."""
    ticker = Ticker.from_dict(SAMPLE_DICT)
    assert isinstance(ticker.highest_52_week_date, datetime)


@pytest.mark.unit
def test_highest_52_week_date_value():
    """highest_52_week_date가 '2022-03-28'과 동일한 날짜 값을 가진다."""
    ticker = Ticker.from_dict(SAMPLE_DICT)
    assert ticker.highest_52_week_date == datetime(2022, 3, 28)


@pytest.mark.unit
def test_lowest_52_week_date_value():
    """lowest_52_week_date가 '2022-12-30'과 동일한 날짜 값을 가진다."""
    ticker = Ticker.from_dict(SAMPLE_DICT)
    assert ticker.lowest_52_week_date == datetime(2022, 12, 30)


@pytest.mark.unit
def test_52_week_fields_none_when_absent():
    """52주 필드가 dict에 없으면 None을 유지한다 (신규 상장 코인 대응)."""
    data = {
        **SAMPLE_DICT,
        "highest_52_week_price": None,
        "highest_52_week_date": None,
        "lowest_52_week_price": None,
        "lowest_52_week_date": None,
    }
    ticker = Ticker.from_dict(data)
    assert ticker.highest_52_week_price is None
    assert ticker.highest_52_week_date is None
    assert ticker.lowest_52_week_price is None
    assert ticker.lowest_52_week_date is None


@pytest.mark.unit
def test_optional_52_week_decimal_fields_are_decimal_when_present():
    """52주 가격 필드가 값이 있을 때 Decimal 타입으로 변환된다."""
    ticker = Ticker.from_dict(SAMPLE_DICT)
    assert isinstance(ticker.highest_52_week_price, Decimal)
    assert isinstance(ticker.lowest_52_week_price, Decimal)


@pytest.mark.unit
def test_post_init_skips_already_datetime():
    """이미 datetime인 필드는 그대로 유지된다."""
    dt = datetime(2023, 2, 21)
    data = {**SAMPLE_DICT, "trade_date": dt}
    ticker = Ticker.from_dict(data)
    assert ticker.trade_date == dt


# ---------------------------------------------------------------------------
# enum 변환
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_change_is_change_side_enum():
    """문자열 change가 ChangeDirection enum으로 변환된다."""
    ticker = Ticker.from_dict(SAMPLE_DICT)
    assert isinstance(ticker.change, ChangeDirection)
    assert ticker.change == ChangeDirection.RISE


@pytest.mark.unit
def test_ask_bid_is_ask_bid_enum():
    """문자열 ask_bid가 AskBid enum으로 변환된다."""
    ticker = Ticker.from_dict(SAMPLE_DICT)
    assert isinstance(ticker.ask_bid, AskBid)
    assert ticker.ask_bid == AskBid.ASK


@pytest.mark.unit
def test_market_state_is_market_state_enum():
    """문자열 market_state가 MarketState enum으로 변환된다."""
    ticker = Ticker.from_dict(SAMPLE_DICT)
    assert isinstance(ticker.market_state, MarketState)
    assert ticker.market_state == MarketState.ACTIVE


@pytest.mark.unit
def test_stream_type_is_stream_type_enum():
    """문자열 stream_type이 StreamType enum으로 변환된다."""
    ticker = Ticker.from_dict(SAMPLE_DICT)
    assert isinstance(ticker.stream_type, StreamType)
    assert ticker.stream_type == StreamType.REALTIME


@pytest.mark.unit
def test_change_fall_parsed():
    """FALL 문자열이 ChangeDirection.FALL로 변환된다."""
    data = {**SAMPLE_DICT, "change": "FALL", "signed_change_price": -404000.0}
    ticker = Ticker.from_dict(data)
    assert ticker.change == ChangeDirection.FALL


@pytest.mark.unit
def test_ask_bid_bid_parsed():
    """BID 문자열이 AskBid.BID로 변환된다."""
    data = {**SAMPLE_DICT, "ask_bid": "BID"}
    ticker = Ticker.from_dict(data)
    assert ticker.ask_bid == AskBid.BID


@pytest.mark.unit
def test_post_init_skips_already_enum():
    """이미 enum인 필드는 그대로 유지된다."""
    data = {**SAMPLE_DICT, "change": ChangeDirection.EVEN, "ask_bid": AskBid.BID}
    ticker = Ticker.from_dict(data)
    assert ticker.change == ChangeDirection.EVEN
    assert ticker.ask_bid == AskBid.BID


@pytest.mark.unit
def test_invalid_change_raises_value_error():
    """정의되지 않은 change 값은 ValueError를 발생시킨다."""
    data = {**SAMPLE_DICT, "change": "UP"}
    with pytest.raises(ValueError):
        Ticker.from_dict(data)


@pytest.mark.unit
def test_invalid_stream_type_raises_value_error():
    """정의되지 않은 stream_type 값은 ValueError를 발생시킨다."""
    data = {**SAMPLE_DICT, "stream_type": "UNKNOWN"}
    with pytest.raises(ValueError):
        Ticker.from_dict(data)


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_to_dict_has_all_keys():
    """to_dict()가 모든 필드 키를 포함한다."""
    ticker = Ticker.from_dict(SAMPLE_DICT)
    result = ticker.to_dict()
    expected_keys = {
        "type",
        "code",
        "opening_price",
        "high_price",
        "low_price",
        "trade_price",
        "prev_closing_price",
        "change",
        "change_price",
        "signed_change_price",
        "change_rate",
        "signed_change_rate",
        "trade_volume",
        "acc_trade_volume",
        "acc_trade_volume_24h",
        "acc_trade_price",
        "acc_trade_price_24h",
        "trade_date",
        "trade_time",
        "trade_timestamp",
        "ask_bid",
        "acc_ask_volume",
        "acc_bid_volume",
        "highest_52_week_price",
        "highest_52_week_date",
        "lowest_52_week_price",
        "lowest_52_week_date",
        "market_state",
        "timestamp",
        "stream_type",
    }
    assert result.keys() == expected_keys


@pytest.mark.unit
def test_to_dict_enum_fields_are_enum_type():
    """to_dict()의 enum 필드는 Enum .value 문자열로 반환된다."""
    result = Ticker.from_dict(SAMPLE_DICT).to_dict()
    assert result["change"] == ChangeDirection.RISE.value
    assert result["ask_bid"] == AskBid.ASK.value
    assert result["market_state"] == MarketState.ACTIVE.value
    assert result["stream_type"] == StreamType.REALTIME.value


@pytest.mark.unit
def test_to_dict_datetime_fields_are_datetime_type():
    """to_dict()의 필수 datetime 필드는 ISO 8601 문자열로 반환된다."""
    result = Ticker.from_dict(SAMPLE_DICT).to_dict()
    assert isinstance(result["trade_date"], str)
    assert isinstance(result["trade_time"], str)


@pytest.mark.unit
def test_to_dict_optional_datetime_fields_when_present():
    """to_dict()의 52주 datetime 필드는 값이 있을 때 ISO 8601 문자열로 반환된다."""
    result = Ticker.from_dict(SAMPLE_DICT).to_dict()
    assert isinstance(result["highest_52_week_date"], str)
    assert isinstance(result["lowest_52_week_date"], str)


@pytest.mark.unit
def test_to_dict_optional_datetime_fields_when_none():
    """to_dict()의 52주 datetime 필드는 None일 때 None으로 반환된다."""
    data = {**SAMPLE_DICT, "highest_52_week_date": None, "lowest_52_week_date": None}
    result = Ticker.from_dict(data).to_dict()
    assert result["highest_52_week_date"] is None
    assert result["lowest_52_week_date"] is None


@pytest.mark.unit
def test_to_dict_decimal_fields_are_decimal_type():
    """to_dict()의 필수 Decimal 필드는 Decimal 타입으로 반환된다."""
    result = Ticker.from_dict(SAMPLE_DICT).to_dict()
    for field in DECIMAL_FIELDS:
        assert isinstance(result[field], Decimal), f"{field} should be Decimal"


@pytest.mark.unit
def test_to_dict_optional_decimal_fields_when_none():
    """to_dict()의 52주 가격 필드는 None일 때 None으로 반환된다."""
    data = {**SAMPLE_DICT, "highest_52_week_price": None, "lowest_52_week_price": None}
    result = Ticker.from_dict(data).to_dict()
    assert result["highest_52_week_price"] is None
    assert result["lowest_52_week_price"] is None


@pytest.mark.unit
def test_to_dict_code_and_timestamps():
    """to_dict()의 code, timestamp, trade_timestamp 값이 원본과 일치한다."""
    result = Ticker.from_dict(SAMPLE_DICT).to_dict()
    assert result["code"] == "KRW-BTC"
    assert result["timestamp"] == SAMPLE_DICT["timestamp"]
    assert result["trade_timestamp"] == SAMPLE_DICT["trade_timestamp"]
