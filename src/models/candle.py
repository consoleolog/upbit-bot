from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from ..utils.constants import CandleType, StreamType
from ..utils.helpers import parse_timeframe
from .base import Base


@dataclass
class Candle(Base):
    """
    업비트 WebSocket 캔들(Candle) 응답 데이터 모델

    Attributes:
        type (str): 캔들 종류 (예: candle.1m, candle.5m 등)
        code (str): 마켓 코드 (예: KRW-BTC)
        candle_date_time_utc (datetime): 캔들 기준 시각 (UTC 기준, ISO 8601 포맷)
        candle_date_time_kst (datetime): 캔들 기준 시각 (KST 기준, ISO 8601 포맷)
        opening_price (Decimal): 시가
        high_price (Decimal): 고가
        low_price (Decimal): 저가
        trade_price (Decimal): 종가 (현재가)
        candle_acc_trade_volume (Decimal): 해당 캔들의 누적 거래량
        candle_acc_trade_price (Decimal): 해당 캔들의 누적 거래 금액
        timestamp (int): 타임스탬프 (밀리초 단위)
        stream_type (str): 스트림 타입 (SNAPSHOT: 스냅샷, REALTIME: 실시간)
    """

    type: CandleType
    code: str
    candle_date_time_utc: datetime
    candle_date_time_kst: datetime
    opening_price: Decimal
    high_price: Decimal
    low_price: Decimal
    trade_price: Decimal
    candle_acc_trade_volume: Decimal
    candle_acc_trade_price: Decimal
    timestamp: int
    stream_type: StreamType

    def __post_init__(self):
        for field_name in [
            "opening_price",
            "high_price",
            "low_price",
            "trade_price",
            "candle_acc_trade_volume",
            "candle_acc_trade_price",
        ]:
            value = getattr(self, field_name)
            if isinstance(value, (int, float, str)):
                setattr(self, field_name, Decimal(str(value)))

        for field_name in ["candle_date_time_utc", "candle_date_time_kst"]:
            value = getattr(self, field_name)
            if isinstance(value, str):
                setattr(self, field_name, datetime.fromisoformat(value))

        if isinstance(self.type, str):
            self.type = CandleType(self.type)
        if isinstance(self.stream_type, str):
            self.stream_type = StreamType(self.stream_type)

    @property
    def is_closed(self) -> bool:
        """캔들 기간이 종료되었는지 여부를 반환합니다."""
        candle_close_time = self.candle_date_time_utc + parse_timeframe(self.type.value)
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        return now_utc >= candle_close_time
