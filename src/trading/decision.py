"""매매 판단 (매매 로직 5). 캔들 마감 직후 마켓 하나의 신호 상태와 보유 정보로 매수·매도 여부를 정한다. 주문은 하지 않는다."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.app.config import AddBuyTrigger, ExitSettings, StopLossBy
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


_MACD_TYPES = (IndicatorType.MACD_SHORT, IndicatorType.MACD_MID, IndicatorType.MACD_LONG)


def decide(
    *,
    states: Mapping[IndicatorType, IndicatorInfo],
    holding: Holding | None,
    candle_time: datetime,
    close: Decimal,
    exit_settings: ExitSettings,
    stochastic_entry_max: float,
    sell_fee_rate: Decimal,
    can_add: bool = False,
    add_buy_trigger: AddBuyTrigger = AddBuyTrigger.ENTRY,
    stop_loss_deferred: bool = False,
    stop_loss_by: StopLossBy = StopLossBy.RATE,
    entry_allowed: bool = True,
) -> Decision | None:
    """판단 순서대로 처음 충족한 매수·매도를 돌려준다. 해당하는 것이 없으면 None (매매 로직 5.1).

    판단할 캔들은 candle_time(시작 시각)이고 close는 그 캔들의 종가다.
    보유 중이면 손절 → 익절 → 과매수 매도 → 데드크로스 매도 → (can_add면) 추가 매수, 미보유면 신규 매수만 본다.
    stop_loss_deferred면 손절하지 않는다 (추가 매수가 남아 있어 복구를 기다릴 때).
    손절은 stop_loss_by에 따른다: RATE는 순수익률이 손절 기준 이하일 때(맨 먼저 판단),
    MACD_DEAD_CROSS는 MACD 3종이 모두 데드크로스일 때(신호 매도 다음, 추가 매수 전에 판단, 수익과 무관).
    추가 매수 조건은 add_buy_trigger에 따른다: ENTRY는 신규 매수와 같은 9개, LOSS_RELAXED는 손실 중 완화된 6개.
    entry_allowed가 False면 신규 매수만 하지 않는다 (스테이지 필터). 추가 매수와 매도는 그대로 판단한다.
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
        if not entry_allowed:
            return None
        return Decision(action=Action.BUY, reason=Reason.ENTRY) if _entry(states, stochastic_entry_max) else None
    rate = net_profit_rate(price=close, average_price=holding.average_price, sell_fee_rate=sell_fee_rate)
    if stop_loss_by is StopLossBy.RATE and rate <= -exit_settings.stop_loss_rate and not stop_loss_deferred:
        return Decision(action=Action.SELL, reason=Reason.STOP_LOSS)
    if rate >= exit_settings.take_profit_rate:
        return Decision(action=Action.SELL, reason=Reason.TAKE_PROFIT)
    # 과매수 매도는 수익과 상관없이 한다. 오른 뒤 꺾이기 전에 빠져나온다 (매매 로직 5.5)
    if _over(states, IndicatorType.STOCHASTIC, OverState.OVER_BOUGHT) and _over(
        states, IndicatorType.RSI, OverState.OVER_BOUGHT
    ):
        return Decision(action=Action.SELL, reason=Reason.OVERBOUGHT_EXIT)
    # 데드크로스 매도는 순수익이 최소 순수익률을 넘을 때만 한다 (매매 로직 5.5)
    if (
        rate > exit_settings.min_profit_rate
        and _cross(states, IndicatorType.STOCHASTIC, CrossState.DEAD_CROSS)
        and _cross(states, IndicatorType.RSI, CrossState.DEAD_CROSS)
    ):
        return Decision(action=Action.SELL, reason=Reason.DEAD_CROSS_EXIT)
    # MACD 손절: 단기·중기·장기가 모두 데드크로스면 하락 추세로 보고 판다 (손실이면 손절, 수익이면 익절이 된다)
    if (
        stop_loss_by is StopLossBy.MACD_DEAD_CROSS
        and not stop_loss_deferred
        and all(_cross(states, t, CrossState.DEAD_CROSS) for t in _MACD_TYPES)
    ):
        return Decision(action=Action.SELL, reason=Reason.STOP_LOSS)
    # 팔지 않으면 추가 매수를 본다. 횟수 한도는 호출하는 쪽이 can_add로 알려 준다 (매매 로직 5.6)
    if can_add:
        if add_buy_trigger is AddBuyTrigger.LOSS_RELAXED:
            # 손실 중일 때만, 완화된 조건으로 사서 평단을 낮춰 복구를 노린다
            add = rate < 0 and relaxed_add_conditions(states)
        else:
            add = _entry(states, stochastic_entry_max)
        if add:
            return Decision(action=Action.BUY, reason=Reason.ADD)
    return None


def entry_conditions(states: Mapping[IndicatorType, IndicatorInfo], stochastic_entry_max: float) -> tuple[bool, ...]:
    """신규·추가 매수 9개 조건 각각의 충족 여부 (매매 로직 5.6). 9개가 모두 참이어야 매수한다.

    순서: RSI 과매도, RSI 골든크로스, 스토캐스틱 과매도, 스토캐스틱 골든크로스, MACD 단기·중기·장기 골든크로스,
    %K 기준 이하, %D 기준 이하. 상태 알림에서 몇 개가 충족됐는지 보여 줄 때도 이 함수를 쓴다.
    """
    golden = CrossState.GOLDEN_CROSS
    stochastic = states.get(IndicatorType.STOCHASTIC)
    # 현재 %K, %D가 기준 이하여야 한다 (반등 초기인지 확인). 값이 없으면 충족하지 않은 것으로 본다
    k = None if stochastic is None else stochastic.value
    d = None if stochastic is None else stochastic.signal
    return (
        _over(states, IndicatorType.RSI, OverState.OVER_SOLD),
        _cross(states, IndicatorType.RSI, golden),
        _over(states, IndicatorType.STOCHASTIC, OverState.OVER_SOLD),
        _cross(states, IndicatorType.STOCHASTIC, golden),
        _cross(states, IndicatorType.MACD_SHORT, golden),
        _cross(states, IndicatorType.MACD_MID, golden),
        _cross(states, IndicatorType.MACD_LONG, golden),
        k is not None and k <= stochastic_entry_max,
        d is not None and d <= stochastic_entry_max,
    )


def relaxed_add_conditions(states: Mapping[IndicatorType, IndicatorInfo]) -> bool:
    """완화된 추가 매수 6개 조건 (숫자 저장소): RSI 골든크로스, 스토캐스틱 과매도·골든크로스, MACD 3종 골든크로스.

    신규 매수 조건에서 RSI 과매도와 %K·%D 기준 이하를 뺀 것이다.
    """
    golden = CrossState.GOLDEN_CROSS
    return (
        _cross(states, IndicatorType.RSI, golden)
        and _over(states, IndicatorType.STOCHASTIC, OverState.OVER_SOLD)
        and _cross(states, IndicatorType.STOCHASTIC, golden)
        and _cross(states, IndicatorType.MACD_SHORT, golden)
        and _cross(states, IndicatorType.MACD_MID, golden)
        and _cross(states, IndicatorType.MACD_LONG, golden)
    )


def _entry(states: Mapping[IndicatorType, IndicatorInfo], stochastic_entry_max: float) -> bool:
    """신규 매수 9개 조건이 모두 참인지 (매매 로직 5.6)."""
    return all(entry_conditions(states, stochastic_entry_max))


def _over(states: Mapping[IndicatorType, IndicatorInfo], indicator_type: IndicatorType, state: OverState) -> bool:
    info = states.get(indicator_type)
    return info is not None and info.over_state == state


def _cross(states: Mapping[IndicatorType, IndicatorInfo], indicator_type: IndicatorType, state: CrossState) -> bool:
    info = states.get(indicator_type)
    return info is not None and info.cross_state == state
