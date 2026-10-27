from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

KST = timezone(timedelta(hours=9), "KST")


@dataclass(frozen=True, kw_only=True)
class Candle:
    """캔들 하나. start는 캔들 시작 시각(UTC), 가격은 원화."""

    market: str
    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    @property
    def start_kst(self) -> datetime:
        """캔들 시작 시각(한국 시간). start와 같은 순간을 한국 시간으로 나타낸다."""
        return self.start.astimezone(KST)
