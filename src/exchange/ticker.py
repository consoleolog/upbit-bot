"""업비트 웹소켓 현재가(ticker) 시세."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.exchange.upbit_client import UpbitResponseError, parse_price

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, kw_only=True)
class Ticker:
    """마켓의 최근 체결가. traded_at은 체결 시각(UTC)이다."""

    market: str
    price: Decimal
    traded_at: datetime


def parse_ticker(raw: object) -> Ticker:
    """업비트 ticker 메시지(JSON 객체) 하나를 Ticker로 바꾼다. 형식이나 값이 잘못되면 UpbitResponseError.

    가격의 정밀도를 지키려면 JSON을 읽을 때 json.loads(..., parse_float=Decimal)로 읽어 넘긴다.
    """
    try:
        if not isinstance(raw, dict) or raw.get("type") != "ticker":
            raise TypeError("message must be a ticker object")
        market = raw["code"]
        if not isinstance(market, str) or not market:
            raise TypeError("code must be a market code")
        price = parse_price(raw["trade_price"])
        timestamp = raw["trade_timestamp"]
        # bool은 int의 하위 타입이라 따로 막는다
        if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= 0:
            raise TypeError("trade_timestamp must be a positive integer in milliseconds")
        # 지나치게 큰 값은 datetime 범위를 넘어 OverflowError가 난다. 잘못된 메시지로 함께 거른다
        traded_at = EPOCH + timedelta(milliseconds=timestamp)
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise UpbitResponseError(f"invalid upbit ticker: {raw!r}") from error
    return Ticker(market=market, price=price, traded_at=traded_at)
