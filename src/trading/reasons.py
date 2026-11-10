from enum import StrEnum


class Action(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class Reason(StrEnum):
    """매매 사유 (매매 로직 5)."""

    ENTRY = "ENTRY"
    ADD = "ADD"  # 보유 중 추가 매수 (매매 로직 5.6)
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    OVERBOUGHT_EXIT = "OVERBOUGHT_EXIT"
    DEAD_CROSS_EXIT = "DEAD_CROSS_EXIT"
