"""DB 행의 모델. Repository는 asyncpg.Record 대신 이 모델을 돌려준다."""

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self

import asyncpg

from src.signals import CrossState, OverState


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
