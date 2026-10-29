"""DB 행의 모델. Repository는 asyncpg.Record 대신 이 모델을 돌려준다."""

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Self

import asyncpg

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
    """모의 포지션. *_time은 판단한 캔들의 시작 시각이다."""

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
        )
