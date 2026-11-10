"""DB 행의 모델. Repository는 asyncpg.Record 대신 이 모델을 돌려준다."""

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Self

import asyncpg

from src.exchange.order import OrderSide
from src.trading.reasons import Reason
from src.trading.signals import CrossState, OverState


@dataclass(frozen=True, kw_only=True)
class Market:
    market_id: int
    exchange: str
    symbol: str
    base_currency: str
    quote_currency: str
    is_active: bool
    created_at: datetime

    @classmethod
    def from_record(cls, row: asyncpg.Record) -> Self:
        return cls(
            market_id=row["market_id"],
            exchange=row["exchange"],
            symbol=row["symbol"],
            base_currency=row["base_currency"],
            quote_currency=row["quote_currency"],
            is_active=row["is_active"],
            created_at=row["created_at"],
        )


class IndicatorType(StrEnum):
    RSI = "RSI"
    STOCHASTIC = "STOCHASTIC"
    MACD_SHORT = "MACD_SHORT"
    MACD_MID = "MACD_MID"
    MACD_LONG = "MACD_LONG"


@dataclass(frozen=True, kw_only=True)
class IndicatorInfo:
    """마켓과 지표 타입별 최신 값과 신호 상태. *_time은 캔들 시작 시각이다."""

    market_id: int
    indicator_type: IndicatorType
    value: float | None
    signal: float | None
    candle_time: datetime
    over_state: OverState | None = None
    over_time: datetime | None = None
    cross_state: CrossState | None = None
    cross_time: datetime | None = None

    def __post_init__(self) -> None:
        # 시간대 없는 시각은 asyncpg가 UTC로 보고 그대로 저장하므로, 잘못된 캔들 시각이 남지 않게 막는다
        for time in (self.candle_time, self.over_time, self.cross_time):
            if time is not None and time.utcoffset() is None:
                raise ValueError(f"indicator info times must be timezone-aware: {self}")
        # NaN, 무한대는 double precision 컬럼이 그대로 받으므로 값이 없으면 None으로만 둔다
        for number in (self.value, self.signal):
            if number is not None and not math.isfinite(number):
                raise ValueError(f"indicator info values must be finite or None: {self}")

    @classmethod
    def from_record(cls, row: asyncpg.Record) -> Self:
        return cls(
            market_id=row["market_id"],
            indicator_type=IndicatorType(row["indicator_type"]),
            value=row["value"],
            signal=row["signal"],
            candle_time=row["candle_time"],
            over_state=None if row["over_state"] is None else OverState(row["over_state"]),
            over_time=row["over_time"],
            cross_state=None if row["cross_state"] is None else CrossState(row["cross_state"]),
            cross_time=row["cross_time"],
        )


class PositionStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


@dataclass(frozen=True, kw_only=True)
class PaperPosition:
    """모의 포지션. entry_time은 매수를 판단한 캔들의 시작 시각, exit_time은 매도를 판단한 캔들의 시작 시각이나
    실시간 손절·익절이면 판단에 쓴 체결 시각이다."""

    position_id: int
    market_id: int
    status: PositionStatus
    entry_time: datetime
    entry_price: Decimal
    average_price: Decimal
    # 매수할 때의 매도 기준(%) (매매 로직 2.3)
    take_profit_rate: Decimal
    stop_loss_rate: Decimal
    min_profit_rate: Decimal
    exit_time: datetime | None
    exit_price: Decimal | None
    exit_reason: Reason | None
    net_profit_rate: Decimal | None
    # 매수 횟수(첫 매수 포함)와 누적 매수 금액(첫 매수 금액을 1로 본 비율) (매매 로직 5.6)
    buy_count: int
    units: Decimal

    @classmethod
    def from_record(cls, row: asyncpg.Record) -> Self:
        return cls(
            position_id=row["position_id"],
            market_id=row["market_id"],
            status=PositionStatus(row["status"]),
            entry_time=row["entry_time"],
            entry_price=row["entry_price"],
            average_price=row["average_price"],
            take_profit_rate=row["take_profit_rate"],
            stop_loss_rate=row["stop_loss_rate"],
            min_profit_rate=row["min_profit_rate"],
            exit_time=row["exit_time"],
            exit_price=row["exit_price"],
            exit_reason=None if row["exit_reason"] is None else Reason(row["exit_reason"]),
            net_profit_rate=row["net_profit_rate"],
            buy_count=row["buy_count"],
            units=row["units"],
        )


class LiveOrderState(StrEnum):
    PENDING = "pending"  # 기록했지만 거래소 접수를 아직 확인하지 못함
    WAIT = "wait"  # 거래소가 접수했고 체결 중
    DONE = "done"
    CANCEL = "cancel"  # 시장가 주문은 일부만 체결되고 남은 양이 취소돼도 cancel이다
    FAILED = "failed"  # 거래소가 접수하지 않음


@dataclass(frozen=True, kw_only=True)
class LiveOrder:
    """실거래 주문. identifier를 업비트 주문의 identifier로 보낸다.

    매수(bid)는 requested_amount(원화), 매도(ask)는 requested_volume(수량)으로 주문한다.
    executed_funds는 체결 금액 합계(수수료 제외), paid_fee는 거래소가 청구한 수수료다.
    """

    order_id: int
    market_id: int
    identifier: str
    side: OrderSide
    reason: Reason
    state: LiveOrderState
    requested_amount: Decimal | None
    requested_volume: Decimal | None
    executed_volume: Decimal
    executed_funds: Decimal
    paid_fee: Decimal
    created_at: datetime

    @classmethod
    def from_record(cls, row: asyncpg.Record) -> Self:
        return cls(
            order_id=row["order_id"],
            market_id=row["market_id"],
            identifier=str(row["identifier"]),
            side=OrderSide(row["side"]),
            reason=Reason(row["reason"]),
            state=LiveOrderState(row["state"]),
            requested_amount=row["requested_amount"],
            requested_volume=row["requested_volume"],
            executed_volume=row["executed_volume"],
            executed_funds=row["executed_funds"],
            paid_fee=row["paid_fee"],
            created_at=row["created_at"],
        )


@dataclass(frozen=True, kw_only=True)
class LivePosition:
    """실거래 포지션. 매수 주문이 체결되면 만들고, 매도 주문이 체결될 때마다 매도 수량과 회수 금액을 더한다.

    entry_cost는 매수 체결 금액 + 매수 수수료, sold_proceeds는 매도 체결 금액 - 매도 수수료다.
    """

    position_id: int
    market_id: int
    status: PositionStatus
    # 매수할 때의 매도 기준(%) (매매 로직 2.3)
    take_profit_rate: Decimal
    stop_loss_rate: Decimal
    min_profit_rate: Decimal
    entry_volume: Decimal
    entry_cost: Decimal
    sold_volume: Decimal
    sold_proceeds: Decimal
    opened_at: datetime
    closed_at: datetime | None
    exit_reason: Reason | None
    # 매수 횟수(첫 매수 포함)와 첫 매수 주문 금액 (매매 로직 5.6, 6.1)
    buy_count: int
    first_buy_amount: Decimal

    @property
    def average_price(self) -> Decimal:
        """매수 수수료를 포함한 평단가 (매매 로직 5.2)."""
        return self.entry_cost / self.entry_volume

    @property
    def holding_volume(self) -> Decimal:
        return self.entry_volume - self.sold_volume

    @classmethod
    def from_record(cls, row: asyncpg.Record) -> Self:
        return cls(
            position_id=row["position_id"],
            market_id=row["market_id"],
            status=PositionStatus(row["status"]),
            take_profit_rate=row["take_profit_rate"],
            stop_loss_rate=row["stop_loss_rate"],
            min_profit_rate=row["min_profit_rate"],
            entry_volume=row["entry_volume"],
            entry_cost=row["entry_cost"],
            sold_volume=row["sold_volume"],
            sold_proceeds=row["sold_proceeds"],
            opened_at=row["opened_at"],
            closed_at=row["closed_at"],
            exit_reason=None if row["exit_reason"] is None else Reason(row["exit_reason"]),
            buy_count=row["buy_count"],
            first_buy_amount=row["first_buy_amount"],
        )
