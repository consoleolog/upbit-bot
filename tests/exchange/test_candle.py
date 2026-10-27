from datetime import UTC, datetime
from decimal import Decimal

from src.exchange.candle import KST, Candle


def test_start_kst_is_same_moment_in_korean_time() -> None:
    candle = Candle(
        market="KRW-BTC",
        start=datetime(2026, 1, 1, 15, 30, tzinfo=UTC),
        open=Decimal(1),
        high=Decimal(1),
        low=Decimal(1),
        close=Decimal(1),
    )

    assert candle.start_kst == datetime(2026, 1, 2, 0, 30, tzinfo=KST)
    assert candle.start_kst.isoformat() == "2026-01-02T00:30:00+09:00"
    assert candle.start_kst == candle.start
