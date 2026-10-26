from dataclasses import dataclass
from decimal import Decimal

from .base import Base


@dataclass
class Asset(Base):
    """
    계정이 보유하고 있는 자산 목록과 잔고

    Attributes:
        currency: 조회하고자 하는 통화 코드
        balance: 주문 가능 수량 또는 금액. (디지털 자산의 경우 수량, 법정 통화(KRW)의 경우 금액입니다.)
        locked: 출금이나 주문 등에 잠겨 있는 잔액
        avg_buy_price: 매수 평균가
        avg_buy_price_modified: 매수 평균가 수정 여부
        unit_currency: 평균가 기준 통화.
                        "avg_buy_price"가 기준하는 단위입니다.
                        [예시] KRW, BTC, USDT
    """

    currency: str
    balance: Decimal
    locked: Decimal
    avg_buy_price: Decimal
    avg_buy_price_modified: bool
    unit_currency: str

    def __post_init__(self):
        for field_name in ["balance", "locked", "avg_buy_price"]:
            value = getattr(self, field_name)
            if isinstance(value, (int, float, str)):
                setattr(self, field_name, Decimal(str(value)))
