"""매매 판단 (매매 로직 5). 캔들 마감 직후 마켓 하나의 신호 상태와 보유 정보로 매수·매도 여부를 정한다. 주문은 하지 않는다."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.app.config import ExitSettings
from src.repositories.models import IndicatorInfo, IndicatorType
from src.trading.reasons import Action, Reason
from src.trading.signals import CrossState, OverState

__all__ = ["Action", "Decision", "Holding", "Reason", "decide", "net_profit_rate"]


@dataclass(frozen=True, kw_only=True)
class Decision:
    action: Action
    reason: Reason


@dataclass(frozen=True, kw_only=True)
class Holding:
    """보유 정보. 평단가는 매수 수수료를 포함한 실제 체결 기준이다 (매매 로직 5.2)."""

    average_price: Decimal

    def __post_init__(self) -> None:
        if not self.average_price > 0:
            raise ValueError(f"average price must be positive: {self.average_price}")


def net_profit_rate(*, price: Decimal, average_price: Decimal, sell_fee_rate: Decimal) -> Decimal:
    """수수료까지 뺀 순수익률(%) = (기준 가격 * (1 - 매도 수수료율) - 평단가) / 평단가 * 100 (매매 로직 5.2)."""
    return (price * (1 - sell_fee_rate) - average_price) / average_price * 100


def decide(
    *,
    states: Mapping[IndicatorType, IndicatorInfo],
    holding: Holding | None,
    candle_time: datetime,
    close: Decimal,
    exit_settings: ExitSettings,
    stochastic_entry_max: float,
    sell_fee_rate: Decimal,
) -> Decision | None:
    """판단 순서대로 처음 충족한 매수·매도를 돌려준다. 해당하는 것이 없으면 None (매매 로직 5.1).

    판단할 캔들은 candle_time(시작 시각)이고 close는 그 캔들의 종가다.
    보유 중이면 손절 → 익절 → 과매수 매도 → 데드크로스 매도, 미보유면 신규 매수만 본다.
    신호 상태가 빠져 있거나 다른 캔들 기준이면 그 상태가 필요한 조건은 충족하지 않은 것으로 본다
    (판단할 수 없으면 사지도 팔지도 않음). 손절·익절은 신호 상태가 필요 없으므로 그대로 판단한다.
    """
    if candle_time.utcoffset() is None:
        raise ValueError(f"candle time must be timezone-aware: {candle_time}")
    if not close > 0:
        raise ValueError(f"close price must be positive: {close}")
    if not 0 <= sell_fee_rate < 1:
        raise ValueError(f"sell fee rate must satisfy 0 <= rate < 1: {sell_fee_rate}")
    # 업비트 반영 지연 등으로 이전 캔들의 상태가 넘어와도 지난 값과 신호로 사고팔지 않게 한다
    states = {t: info for t, info in states.items() if info.candle_time == candle_time}
    if holding is None:
        return Decision(action=Action.BUY, reason=Reason.ENTRY) if _entry(states, stochastic_entry_max) else None
    rate = net_profit_rate(price=close, average_price=holding.average_price, sell_fee_rate=sell_fee_rate)
    if rate <= -exit_settings.stop_loss_rate:
        return Decision(action=Action.SELL, reason=Reason.STOP_LOSS)
    if rate >= exit_settings.take_profit_rate:
        return Decision(action=Action.SELL, reason=Reason.TAKE_PROFIT)
    # 신호 매도는 순수익일 때만 한다. 손실 상태의 매도는 손절로만 한다 (매매 로직 5.5)
    if rate <= exit_settings.min_profit_rate:
        return None
    if _over(states, IndicatorType.STOCHASTIC, OverState.OVER_BOUGHT) and _over(
        states, IndicatorType.RSI, OverState.OVER_BOUGHT
    ):
        return Decision(action=Action.SELL, reason=Reason.OVERBOUGHT_EXIT)
    if _cross(states, IndicatorType.STOCHASTIC, CrossState.DEAD_CROSS) and _cross(
        states, IndicatorType.RSI, CrossState.DEAD_CROSS
    ):
        return Decision(action=Action.SELL, reason=Reason.DEAD_CROSS_EXIT)
    return None


def _entry(states: Mapping[IndicatorType, IndicatorInfo], stochastic_entry_max: float) -> bool:
    """신규 매수 9개 조건이 모두 참인지 (매매 로직 5.6)."""
    stochastic = states.get(IndicatorType.STOCHASTIC)
    # 현재 %K, %D가 기준 이하여야 한다 (반등 초기인지 확인). 값이 없으면 판단하지 않는다
    if stochastic is None or stochastic.value is None or stochastic.signal is None:
        return False
    if stochastic.value > stochastic_entry_max or stochastic.signal > stochastic_entry_max:
        return False
    golden = CrossState.GOLDEN_CROSS
    return (
        _over(states, IndicatorType.RSI, OverState.OVER_SOLD)
        and _cross(states, IndicatorType.RSI, golden)
        and _over(states, IndicatorType.STOCHASTIC, OverState.OVER_SOLD)
        and _cross(states, IndicatorType.STOCHASTIC, golden)
        and _cross(states, IndicatorType.MACD_SHORT, golden)
        and _cross(states, IndicatorType.MACD_MID, golden)
        and _cross(states, IndicatorType.MACD_LONG, golden)
    )


def _over(states: Mapping[IndicatorType, IndicatorInfo], indicator_type: IndicatorType, state: OverState) -> bool:
    info = states.get(indicator_type)
    return info is not None and info.over_state == state


def _cross(states: Mapping[IndicatorType, IndicatorInfo], indicator_type: IndicatorType, state: CrossState) -> bool:
    info = states.get(indicator_type)
    return info is not None and info.cross_state == state
