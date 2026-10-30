import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.exchange.ticker import Ticker, parse_ticker
from src.exchange.upbit_client import UpbitResponseError

# 업비트 웹소켓에서 실제로 받은 ticker 메시지 (일부 필드 생략)
MESSAGE = (
    b'{"type":"ticker","code":"KRW-BTC","opening_price":105354000.00000000,"high_price":106111000.00000000,'
    b'"low_price":103711000.00000000,"trade_price":104438000.00000000,"prev_closing_price":105354000.00000000,'
    b'"change":"FALL","signed_change_rate":-0.0086944966,"ask_bid":"ASK","trade_volume":0.08106847,'
    b'"trade_date":"20261030","trade_time":"105432","trade_timestamp":1793357672361,"market_state":"ACTIVE",'
    b'"is_trading_suspended":false,"timestamp":1793357672517,"stream_type":"SNAPSHOT"}'
)


def _message(**changes: object) -> dict[str, object]:
    raw = json.loads(MESSAGE, parse_float=Decimal)
    for key, value in changes.items():
        if value is None:
            del raw[key]
        else:
            raw[key] = value
    return raw


def test_parse_real_ticker_message() -> None:
    ticker = parse_ticker(json.loads(MESSAGE, parse_float=Decimal))

    assert ticker == Ticker(
        market="KRW-BTC", price=Decimal("104438000"), traded_at=datetime(2026, 10, 30, 10, 54, 32, 361000, tzinfo=UTC)
    )
    # 업비트의 trade_date·trade_time(UTC)과 같은 시각이다
    assert ticker.traded_at.strftime("%Y%m%d %H%M%S") == "20261030 105432"


def test_price_keeps_decimal_precision() -> None:
    ticker = parse_ticker(_message(trade_price=Decimal("0.00012345")))

    assert ticker.price == Decimal("0.00012345")


def test_float_price_is_read_as_written() -> None:
    # parse_float=Decimal 없이 읽은 float도 표기된 값 그대로 바꾼다
    ticker = parse_ticker(_message(trade_price=1692.1))

    assert ticker.price == Decimal("1692.1")


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        "ticker",
        _message(type="trade"),
        _message(type=None),
        _message(code=None),
        _message(code=""),
        _message(code=1),
        _message(trade_price=None),
        _message(trade_price=0),
        _message(trade_price=-1),
        _message(trade_price="104438000"),
        _message(trade_price=True),
        _message(trade_price=Decimal("Infinity")),
        _message(trade_price=Decimal("NaN")),
        _message(trade_timestamp=None),
        _message(trade_timestamp="1793357672361"),
        _message(trade_timestamp=1793357672361.5),
        _message(trade_timestamp=True),
        _message(trade_timestamp=0),
        _message(trade_timestamp=-1),
        # datetime 범위를 넘는 값
        _message(trade_timestamp=10**20),
    ],
)
def test_invalid_ticker_rejected(raw: object) -> None:
    with pytest.raises(UpbitResponseError, match="invalid upbit ticker"):
        parse_ticker(raw)
