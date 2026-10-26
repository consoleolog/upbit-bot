from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ..utils.constants import SignalDirection
from .base import Base


@dataclass
class Position(Base):
    """
    보유 중인 포지션 모델

    Attributes:
        market (str): 마켓 코드 (예: KRW-BTC)
        direction (SignalDirection): 포지션 방향 (LONG: 매수, SHORT: 매도)
        entry_price (Decimal): 진입 평균 단가
        current_price (Decimal): 현재 시장 가격
        volume (Decimal): 보유 수량
        stop_loss (Decimal): 손절 가격
        take_profit (Decimal): 익절 가격
        strategy_id (str): 이 포지션을 생성한 전략의 식별자
        opened_at (datetime): 포지션 진입 시각
    """

    market: str
    direction: SignalDirection
    entry_price: Decimal
    current_price: Decimal
    volume: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    strategy_id: str
    opened_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        if isinstance(self.direction, str):
            self.direction = SignalDirection(self.direction)

        for field_name in ["entry_price", "current_price", "volume", "stop_loss", "take_profit"]:
            value = getattr(self, field_name)
            if isinstance(value, (int, float, str)):
                setattr(self, field_name, Decimal(str(value)))

        if isinstance(self.opened_at, str):
            self.opened_at = datetime.fromisoformat(self.opened_at)

    @property
    def unrealized_pnl(self) -> Decimal:
        """미실현 손익 (체결 가격 기준)."""
        if self.direction == SignalDirection.LONG:
            return (self.current_price - self.entry_price) * self.volume
        else:
            return (self.entry_price - self.current_price) * self.volume

    @property
    def unrealized_pnl_percent(self) -> float:
        """미실현 손익률 (진입가 대비 비율)."""
        if self.entry_price == 0:
            return 0.0
        if self.direction == SignalDirection.LONG:
            return float((self.current_price - self.entry_price) / self.entry_price)
        else:
            return float((self.entry_price - self.current_price) / self.entry_price)

    @property
    def value(self) -> Decimal:
        """현재 포지션 평가금액 (현재가 X 수량)."""
        return self.volume * self.current_price
