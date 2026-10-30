"""업비트 계좌·주문 가능 정보. 금액과 수량은 Decimal이다."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, kw_only=True)
class Account:
    """화폐 하나의 잔고. balance는 주문 가능 수량, locked는 주문 중이라 묶인 수량이다."""

    currency: str
    balance: Decimal
    locked: Decimal
    avg_buy_price: Decimal
    unit_currency: str


@dataclass(frozen=True, kw_only=True)
class OrderChance:
    """마켓의 주문 가능 정보. 수수료는 비율(0.0005 = 0.05%), 최소·최대 주문 금액은 호가 통화(원) 기준이다."""

    market: str
    bid_fee: Decimal
    ask_fee: Decimal
    bid_min_total: Decimal
    ask_min_total: Decimal
    max_total: Decimal
    state: str
