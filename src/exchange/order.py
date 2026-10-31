"""업비트 주문. 금액과 수량은 Decimal, 시각은 시간대 있는 datetime이다."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

# 업비트 문서 "주문 생성": identifier는 계정 전체에서 유일해야 하고 최대 64자다
MAX_IDENTIFIER_LENGTH = 64


class OrderSide(StrEnum):
    BID = "bid"  # 매수
    ASK = "ask"  # 매도


class OrderType(StrEnum):
    PRICE = "price"  # 시장가 매수 (총액 지정)
    MARKET = "market"  # 시장가 매도 (수량 지정)
    LIMIT = "limit"
    BEST = "best"


class OrderState(StrEnum):
    WAIT = "wait"  # 체결 대기
    WATCH = "watch"  # 예약 주문 대기
    DONE = "done"  # 체결 완료
    CANCEL = "cancel"  # 주문 취소. 시장가 주문은 일부만 체결되고 남은 양이 취소돼도 cancel이다


@dataclass(frozen=True, kw_only=True)
class Trade:
    """주문의 체결 한 건. funds는 체결 금액(가격 x 수량)이다."""

    price: Decimal
    volume: Decimal
    funds: Decimal
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class Order:
    """업비트 주문.

    체결된 양은 state가 아니라 executed_volume으로 판단한다 (cancel이어도 일부 체결됐을 수 있다).
    trades는 주문 조회에만 오고, 주문 생성 응답에는 오지 않아 비어 있다.
    """

    uuid: str
    identifier: str | None
    market: str
    side: OrderSide
    ord_type: OrderType
    state: OrderState
    created_at: datetime
    price: Decimal | None  # 시장가 매수의 주문 총액. 시장가 매도에는 없다
    volume: Decimal | None  # 시장가 매도의 주문 수량. 시장가 매수에는 없다
    executed_volume: Decimal
    paid_fee: Decimal
    trades_count: int
    trades: tuple[Trade, ...]


def validate_identifier(identifier: str) -> None:
    """주문 identifier를 검사한다. 비었거나 너무 길면 ValueError (업비트가 거절하기 전에 막는다)."""
    if not identifier or len(identifier) > MAX_IDENTIFIER_LENGTH:
        raise ValueError(f"identifier must be 1 to {MAX_IDENTIFIER_LENGTH} characters: {identifier!r}")
