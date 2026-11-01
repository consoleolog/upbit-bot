"""업비트 계좌·주문 가능 정보. 금액과 수량은 Decimal이다."""

from dataclasses import dataclass
from decimal import Decimal

# 업비트 주문 가능 정보의 market.state 중 주문할 수 있는 상태
MARKET_STATE_ACTIVE = "active"


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
