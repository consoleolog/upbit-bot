from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.app.config import AddBuyTrigger, ExitSettings, StopLossBy
from src.repositories.models import IndicatorInfo, IndicatorType
from src.trading.decision import (
    Action,
    Decision,
    Holding,
    Reason,
    decide,
    entry_conditions,
    net_profit_rate,
    relaxed_add_conditions,
)
from src.trading.signals import CrossState, OverState

T0 = datetime(2026, 1, 1, tzinfo=UTC)
EXIT = ExitSettings(take_profit_rate=Decimal("1.5"), stop_loss_rate=Decimal("3.0"), min_profit_rate=Decimal("0.5"))
HOLDING = Holding(average_price=Decimal("100"))
NO_FEE = Decimal("0")
MACD_TYPES = (IndicatorType.MACD_SHORT, IndicatorType.MACD_MID, IndicatorType.MACD_LONG)

BUY = Decision(action=Action.BUY, reason=Reason.ENTRY)


def _info(indicator_type: IndicatorType, *, value: float | None = 50.0, signal: float | None = 50.0) -> IndicatorInfo:
    return IndicatorInfo(market_id=1, indicator_type=indicator_type, value=value, signal=signal, candle_time=T0)


def _with(info: IndicatorInfo, *, over: OverState | None = None, cross: CrossState | None = None) -> IndicatorInfo:
    return replace(
        info, over_state=over, over_time=T0 if over else None, cross_state=cross, cross_time=T0 if cross else None
    )


def _entry_states() -> dict[IndicatorType, IndicatorInfo]:
    """신규 매수 9개 조건이 모두 참인 신호 상태."""
    golden, sold = CrossState.GOLDEN_CROSS, OverState.OVER_SOLD
    states = {
        IndicatorType.RSI: _with(_info(IndicatorType.RSI, value=35.0), over=sold, cross=golden),
        IndicatorType.STOCHASTIC: _with(
            _info(IndicatorType.STOCHASTIC, value=40.0, signal=30.0), over=sold, cross=golden
        ),
    }
    return states | {t: _with(_info(t), cross=golden) for t in MACD_TYPES}


def _decide(
    states: dict[IndicatorType, IndicatorInfo],
    holding: Holding | None = None,
    close: str = "100",
    fee: Decimal = NO_FEE,
) -> Decision | None:
    return decide(
        states=states,
        holding=holding,
        candle_time=T0,
        close=Decimal(close),
        exit_settings=EXIT,
        stochastic_entry_max=50.0,
        sell_fee_rate=fee,
    )


# ---------------------------------------------------------------------------
# 순수익률 (매매 로직 5.2)
# ---------------------------------------------------------------------------


def test_net_profit_rate_subtracts_sell_fee() -> None:
    rate = net_profit_rate(price=Decimal("101.5"), average_price=Decimal("100"), sell_fee_rate=Decimal("0.0005"))

    # 101.5 * 0.9995 = 101.44925 → 1.44925%
    assert rate == Decimal("1.44925")


# ---------------------------------------------------------------------------
# 신규 매수 (매매 로직 5.6)
# ---------------------------------------------------------------------------


def test_buy_when_all_nine_conditions_hold() -> None:
    assert _decide(_entry_states()) == BUY


def _without_condition(name: str) -> dict[IndicatorType, IndicatorInfo]:
    states = _entry_states()
    rsi, stochastic = states[IndicatorType.RSI], states[IndicatorType.STOCHASTIC]
    changes = {
        "rsi over sold": {IndicatorType.RSI: replace(rsi, over_state=None, over_time=None)},
        "rsi golden": {IndicatorType.RSI: replace(rsi, cross_state=CrossState.DEAD_CROSS)},
        "stochastic over sold": {IndicatorType.STOCHASTIC: replace(stochastic, over_state=OverState.OVER_BOUGHT)},
        "stochastic golden": {IndicatorType.STOCHASTIC: replace(stochastic, cross_state=None, cross_time=None)},
        "macd short golden": {IndicatorType.MACD_SHORT: _info(IndicatorType.MACD_SHORT)},
        "macd mid golden": {IndicatorType.MACD_MID: _with(_info(IndicatorType.MACD_MID), cross=CrossState.DEAD_CROSS)},
        "macd long golden": {IndicatorType.MACD_LONG: _info(IndicatorType.MACD_LONG)},
        "stochastic %D <= max": {IndicatorType.STOCHASTIC: replace(stochastic, signal=50.1)},
        "stochastic %K <= max": {IndicatorType.STOCHASTIC: replace(stochastic, value=50.1)},
    }
    return states | changes[name]


@pytest.mark.parametrize(
    "condition",
    [
        "rsi over sold",
        "rsi golden",
        "stochastic over sold",
        "stochastic golden",
        "macd short golden",
        "macd mid golden",
        "macd long golden",
        "stochastic %D <= max",
        "stochastic %K <= max",
    ],
)
def test_no_buy_when_any_condition_fails(condition: str) -> None:
    assert _decide(_without_condition(condition)) is None


def test_stochastic_exactly_at_entry_max_buys() -> None:
    states = _entry_states()
    states[IndicatorType.STOCHASTIC] = replace(states[IndicatorType.STOCHASTIC], value=50.0, signal=50.0)

    assert _decide(states) == BUY


@pytest.mark.parametrize("field", ["value", "signal"])
def test_no_buy_without_stochastic_value(field: str) -> None:
    states = _entry_states()
    states[IndicatorType.STOCHASTIC] = replace(states[IndicatorType.STOCHASTIC], **{field: None})

    assert _decide(states) is None


@pytest.mark.parametrize("missing", list(IndicatorType))
def test_no_buy_when_indicator_state_missing(missing: IndicatorType) -> None:
    states = _entry_states()
    del states[missing]

    assert _decide(states) is None


def test_holding_never_buys_again() -> None:
    # 마켓당 포지션은 하나. 보유 중에는 매수 조건이 모두 참이어도 사지 않는다
    assert _decide(_entry_states(), HOLDING) is None


# ---------------------------------------------------------------------------
# 손절·익절 (매매 로직 5.3, 5.4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("close", "expected"),
    [
        ("97", Reason.STOP_LOSS),  # -3.0%: 기준과 같으면 손절
        ("90", Reason.STOP_LOSS),
        ("97.01", None),
        ("101.5", Reason.TAKE_PROFIT),  # +1.5%: 기준과 같으면 익절
        ("120", Reason.TAKE_PROFIT),
        ("101.49", None),
    ],
)
def test_stop_loss_and_take_profit_boundaries(close: str, expected: Reason | None) -> None:
    decision = _decide({}, HOLDING, close)

    assert decision == (None if expected is None else Decision(action=Action.SELL, reason=expected))


def test_fee_is_included_in_take_profit() -> None:
    # 수수료를 빼면 1.44925%로 익절 기준(1.5%)에 못 미친다
    assert _decide({}, HOLDING, "101.5", Decimal("0.0005")) is None


def test_stop_loss_comes_before_signal_sell() -> None:
    states = _overbought_states()

    assert _decide(states, HOLDING, "96") == Decision(action=Action.SELL, reason=Reason.STOP_LOSS)


def test_take_profit_comes_before_signal_sell() -> None:
    states = _overbought_states()

    assert _decide(states, HOLDING, "102") == Decision(action=Action.SELL, reason=Reason.TAKE_PROFIT)


# ---------------------------------------------------------------------------
# 신호 매도 (매매 로직 5.5)
# ---------------------------------------------------------------------------


def _overbought_states() -> dict[IndicatorType, IndicatorInfo]:
    bought = OverState.OVER_BOUGHT
    return {t: _with(_info(t), over=bought) for t in (IndicatorType.RSI, IndicatorType.STOCHASTIC)}


def _dead_cross_states() -> dict[IndicatorType, IndicatorInfo]:
    dead = CrossState.DEAD_CROSS
    return {t: _with(_info(t), cross=dead) for t in (IndicatorType.RSI, IndicatorType.STOCHASTIC)}


@pytest.mark.parametrize("close", ["100.6", "100.5", "100", "98"])
def test_overbought_exit_regardless_of_profit(close: str) -> None:
    # 과매수 매도는 손실 중이어도 한다 (매매 로직 5.5). 손절 기준(-3%) 안쪽이다
    assert _decide(_overbought_states(), HOLDING, close) == Decision(action=Action.SELL, reason=Reason.OVERBOUGHT_EXIT)


def test_dead_cross_exit_above_min_profit() -> None:
    assert _decide(_dead_cross_states(), HOLDING, "100.6") == Decision(
        action=Action.SELL, reason=Reason.DEAD_CROSS_EXIT
    )


@pytest.mark.parametrize("close", ["100.5", "100", "98"])
def test_no_dead_cross_exit_at_or_below_min_profit(close: str) -> None:
    # 데드크로스 매도는 순수익률이 최소 순수익률(0.5%)을 넘어야 한다
    assert _decide(_dead_cross_states(), HOLDING, close) is None


def test_stop_loss_comes_before_overbought_exit() -> None:
    assert _decide(_overbought_states(), HOLDING, "96.9") == Decision(action=Action.SELL, reason=Reason.STOP_LOSS)


def test_overbought_exit_comes_before_dead_cross_exit() -> None:
    states = {
        t: _with(_info(t), over=OverState.OVER_BOUGHT, cross=CrossState.DEAD_CROSS)
        for t in (IndicatorType.RSI, IndicatorType.STOCHASTIC)
    }

    assert _decide(states, HOLDING, "100.6") == Decision(action=Action.SELL, reason=Reason.OVERBOUGHT_EXIT)


@pytest.mark.parametrize("only", [IndicatorType.RSI, IndicatorType.STOCHASTIC])
def test_signal_sell_needs_both_rsi_and_stochastic(only: IndicatorType) -> None:
    assert _decide({only: _overbought_states()[only]}, HOLDING, "100.6") is None
    assert _decide({only: _dead_cross_states()[only]}, HOLDING, "100.6") is None


def test_holding_without_any_condition_holds() -> None:
    assert _decide(_entry_states(), HOLDING, "100.6") is None


# ---------------------------------------------------------------------------
# 잘못된 입력
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("price", ["0", "-1"])
def test_invalid_average_price_rejected(price: str) -> None:
    with pytest.raises(ValueError, match="average price"):
        Holding(average_price=Decimal(price))


@pytest.mark.parametrize("close", ["0", "-100"])
def test_invalid_close_rejected(close: str) -> None:
    with pytest.raises(ValueError, match="close price"):
        _decide({}, HOLDING, close)


# ---------------------------------------------------------------------------
# 다른 캔들 기준의 신호 상태
# ---------------------------------------------------------------------------

EARLIER = T0 - timedelta(minutes=30)


def _from_earlier_candle(states: dict[IndicatorType, IndicatorInfo]) -> dict[IndicatorType, IndicatorInfo]:
    return {t: replace(info, candle_time=EARLIER) for t, info in states.items()}


def test_no_buy_with_states_from_earlier_candle() -> None:
    assert _decide(_from_earlier_candle(_entry_states())) is None


def test_no_buy_when_one_state_is_from_earlier_candle() -> None:
    states = _entry_states()
    states[IndicatorType.MACD_LONG] = replace(states[IndicatorType.MACD_LONG], candle_time=EARLIER)

    assert _decide(states) is None


def test_no_signal_sell_with_states_from_earlier_candle() -> None:
    assert _decide(_from_earlier_candle(_overbought_states()), HOLDING, "100.6") is None


def test_stop_loss_and_take_profit_do_not_need_current_states() -> None:
    # 손실을 막는 판단은 신호 상태가 오래돼도 막히지 않는다
    stale = _from_earlier_candle(_overbought_states())

    assert _decide(stale, HOLDING, "96") == Decision(action=Action.SELL, reason=Reason.STOP_LOSS)
    assert _decide(stale, HOLDING, "102") == Decision(action=Action.SELL, reason=Reason.TAKE_PROFIT)


def test_naive_candle_time_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        decide(
            states=_entry_states(),
            holding=None,
            candle_time=datetime(2026, 1, 1),
            close=Decimal("100"),
            exit_settings=EXIT,
            stochastic_entry_max=50.0,
            sell_fee_rate=NO_FEE,
        )


@pytest.mark.parametrize("fee", [Decimal("-0.0001"), Decimal("1")])
def test_invalid_fee_rejected(fee: Decimal) -> None:
    with pytest.raises(ValueError, match="fee rate"):
        _decide({}, HOLDING, "100", fee)


# ---------------------------------------------------------------------------
# 추가 매수 (매매 로직 5.6)
# ---------------------------------------------------------------------------


def _decide_holding(states: dict[IndicatorType, IndicatorInfo], close: str, *, can_add: bool) -> Decision | None:
    return decide(
        states=states,
        holding=HOLDING,
        candle_time=T0,
        close=Decimal(close),
        exit_settings=EXIT,
        stochastic_entry_max=50.0,
        sell_fee_rate=NO_FEE,
        can_add=can_add,
    )


def test_add_buy_with_same_conditions_as_entry() -> None:
    assert _decide_holding(_entry_states(), "99", can_add=True) == Decision(action=Action.BUY, reason=Reason.ADD)


def test_no_add_buy_without_permission() -> None:
    # 횟수 한도에 닿았거나 추가 매수를 쓰지 않으면 호출하는 쪽이 can_add=False를 넘긴다
    assert _decide_holding(_entry_states(), "99", can_add=False) is None


@pytest.mark.parametrize("name", ["rsi over sold", "stochastic golden", "macd long golden", "stochastic %K <= max"])
def test_add_buy_needs_all_entry_conditions(name: str) -> None:
    assert _decide_holding(_without_condition(name), "99", can_add=True) is None


def test_stop_loss_comes_before_add_buy() -> None:
    assert _decide_holding(_entry_states(), "96.9", can_add=True) == Decision(
        action=Action.SELL, reason=Reason.STOP_LOSS
    )


# ---------------------------------------------------------------------------
# 매수 조건 하나씩 (상태 알림에 쓴다)
# ---------------------------------------------------------------------------


def test_entry_conditions_all_true_when_buy() -> None:
    assert entry_conditions(_entry_states(), 50.0) == (True,) * 9


def test_entry_conditions_without_states_all_false() -> None:
    assert entry_conditions({}, 50.0) == (False,) * 9


@pytest.mark.parametrize(
    ("change", "index"),
    [
        (lambda s: s | {IndicatorType.RSI: replace(s[IndicatorType.RSI], over_state=None)}, 0),
        (lambda s: s | {IndicatorType.RSI: replace(s[IndicatorType.RSI], cross_state=CrossState.DEAD_CROSS)}, 1),
        (lambda s: s | {IndicatorType.STOCHASTIC: replace(s[IndicatorType.STOCHASTIC], over_state=None)}, 2),
        (lambda s: s | {IndicatorType.STOCHASTIC: replace(s[IndicatorType.STOCHASTIC], cross_state=None)}, 3),
        (lambda s: s | {IndicatorType.MACD_SHORT: replace(s[IndicatorType.MACD_SHORT], cross_state=None)}, 4),
        (lambda s: s | {IndicatorType.MACD_MID: replace(s[IndicatorType.MACD_MID], cross_state=None)}, 5),
        (lambda s: s | {IndicatorType.MACD_LONG: replace(s[IndicatorType.MACD_LONG], cross_state=None)}, 6),
        (lambda s: s | {IndicatorType.STOCHASTIC: replace(s[IndicatorType.STOCHASTIC], value=50.1)}, 7),
        (lambda s: s | {IndicatorType.STOCHASTIC: replace(s[IndicatorType.STOCHASTIC], signal=None)}, 8),
    ],
)
def test_each_entry_condition_is_counted_and_blocks_buy(change: object, index: int) -> None:
    states = change(_entry_states())  # type: ignore[operator]

    conditions = entry_conditions(states, 50.0)

    # 바꾼 조건 하나만 거짓이고, 그 하나 때문에 매수하지 않는다
    assert [i for i, ok in enumerate(conditions) if not ok] == [index]
    assert _decide(states) is None


# ---------------------------------------------------------------------------
# 손실 중 완화된 추가 매수, 추가 매수가 남아 있는 동안 손절 미루기
# ---------------------------------------------------------------------------


def _relaxed_states() -> dict[IndicatorType, IndicatorInfo]:
    """완화된 6개 조건만 참인 상태: RSI 과매도 없음, %K·%D가 50 초과라 신규 매수 9개 조건은 거짓."""
    golden, sold = CrossState.GOLDEN_CROSS, OverState.OVER_SOLD
    states = {
        IndicatorType.RSI: _with(_info(IndicatorType.RSI, value=45.0), cross=golden),
        IndicatorType.STOCHASTIC: _with(
            _info(IndicatorType.STOCHASTIC, value=60.0, signal=55.0), over=sold, cross=golden
        ),
    }
    return states | {t: _with(_info(t), cross=golden) for t in MACD_TYPES}


def _decide_add(
    states: dict[IndicatorType, IndicatorInfo],
    close: str,
    *,
    trigger: AddBuyTrigger = AddBuyTrigger.LOSS_RELAXED,
    can_add: bool = True,
    deferred: bool = False,
) -> Decision | None:
    return decide(
        states=states,
        holding=HOLDING,
        candle_time=T0,
        close=Decimal(close),
        exit_settings=EXIT,
        stochastic_entry_max=50.0,
        sell_fee_rate=NO_FEE,
        can_add=can_add,
        add_buy_trigger=trigger,
        stop_loss_deferred=deferred,
    )


ADD = Decision(action=Action.BUY, reason=Reason.ADD)


@pytest.mark.parametrize("close", ["99.99", "98", "97.01"])
def test_loss_relaxed_add_when_losing(close: str) -> None:
    assert relaxed_add_conditions(_relaxed_states())
    assert _decide_add(_relaxed_states(), close) == ADD


@pytest.mark.parametrize("close", ["100", "101"])
def test_no_loss_relaxed_add_without_loss(close: str) -> None:
    # 손실 중일 때만 평단을 낮추려고 산다
    assert _decide_add(_relaxed_states(), close) is None


def test_entry_trigger_does_not_use_relaxed_conditions() -> None:
    # 기본(ENTRY)은 9개 조건이라, RSI 과매도가 없고 %K·%D가 50을 넘으면 사지 않는다
    assert _decide_add(_relaxed_states(), "98", trigger=AddBuyTrigger.ENTRY) is None
    assert _decide_add(_entry_states(), "98", trigger=AddBuyTrigger.ENTRY) == ADD


@pytest.mark.parametrize(
    "drop",
    [
        lambda s: s | {IndicatorType.RSI: replace(s[IndicatorType.RSI], cross_state=None)},
        lambda s: s | {IndicatorType.STOCHASTIC: replace(s[IndicatorType.STOCHASTIC], over_state=None)},
        lambda s: s | {IndicatorType.STOCHASTIC: replace(s[IndicatorType.STOCHASTIC], cross_state=None)},
        lambda s: s | {IndicatorType.MACD_SHORT: replace(s[IndicatorType.MACD_SHORT], cross_state=None)},
        lambda s: s | {IndicatorType.MACD_MID: replace(s[IndicatorType.MACD_MID], cross_state=None)},
        lambda s: s | {IndicatorType.MACD_LONG: replace(s[IndicatorType.MACD_LONG], cross_state=None)},
    ],
    ids=["rsi-cross", "stochastic-over", "stochastic-cross", "macd-short", "macd-mid", "macd-long"],
)
def test_no_loss_relaxed_add_without_each_condition(drop: object) -> None:
    states = drop(_relaxed_states())  # type: ignore[operator]

    assert not relaxed_add_conditions(states)
    assert _decide_add(states, "98") is None


def test_no_loss_relaxed_add_when_adds_used_up() -> None:
    assert _decide_add(_relaxed_states(), "98", can_add=False) is None


def test_deferred_stop_loss_does_not_sell() -> None:
    # 추가 매수가 남아 있으면 손절 기준(-3%) 아래여도 팔지 않고, 조건이 맞으면 추가 매수한다
    assert _decide_add({}, "96", deferred=True) is None
    assert _decide_add(_relaxed_states(), "96", deferred=True) == ADD


def test_stop_loss_when_not_deferred() -> None:
    assert _decide_add(_relaxed_states(), "96") == Decision(action=Action.SELL, reason=Reason.STOP_LOSS)


def test_deferred_stop_loss_keeps_take_profit_and_signal_exits() -> None:
    assert _decide_add({}, "101.6", deferred=True) == Decision(action=Action.SELL, reason=Reason.TAKE_PROFIT)
    assert _decide_add(_overbought_states(), "96", deferred=True) == Decision(
        action=Action.SELL, reason=Reason.OVERBOUGHT_EXIT
    )


# ---------------------------------------------------------------------------
# MACD 3종 데드크로스 손절 (고정 % 손절 대신)
# ---------------------------------------------------------------------------

MACD_STOP = Decision(action=Action.SELL, reason=Reason.STOP_LOSS)


def _macd_dead() -> dict[IndicatorType, IndicatorInfo]:
    return {t: _with(_info(t), cross=CrossState.DEAD_CROSS) for t in MACD_TYPES}


def _decide_macd_stop(
    states: dict[IndicatorType, IndicatorInfo], close: str, *, deferred: bool = False, can_add: bool = False
) -> Decision | None:
    return decide(
        states=states,
        holding=HOLDING,
        candle_time=T0,
        close=Decimal(close),
        exit_settings=EXIT,
        stochastic_entry_max=50.0,
        sell_fee_rate=NO_FEE,
        can_add=can_add,
        add_buy_trigger=AddBuyTrigger.LOSS_RELAXED,
        stop_loss_deferred=deferred,
        stop_loss_by=StopLossBy.MACD_DEAD_CROSS,
    )


@pytest.mark.parametrize("close", ["90", "96", "99", "100.5", "101.4"])
def test_macd_stop_sells_when_all_three_dead_regardless_of_profit(close: str) -> None:
    assert _decide_macd_stop(_macd_dead(), close) == MACD_STOP


@pytest.mark.parametrize("close", ["90", "50"])
def test_macd_stop_ignores_fixed_rate(close: str) -> None:
    # 고정 % 손절은 하지 않는다. MACD가 모두 꺾이기 전에는 크게 떨어져도 들고 있다
    assert _decide_macd_stop({}, close) is None


@pytest.mark.parametrize("alive", MACD_TYPES)
def test_no_macd_stop_unless_all_three_dead(alive: IndicatorType) -> None:
    states = _macd_dead() | {alive: _with(_info(alive), cross=CrossState.GOLDEN_CROSS)}

    assert _decide_macd_stop(states, "96") is None


def test_macd_stop_waits_while_adds_left() -> None:
    assert _decide_macd_stop(_macd_dead(), "96", deferred=True) is None


def test_signal_exits_come_before_macd_stop() -> None:
    assert _decide_macd_stop(_macd_dead() | _overbought_states(), "96") == Decision(
        action=Action.SELL, reason=Reason.OVERBOUGHT_EXIT
    )
    assert _decide_macd_stop(_macd_dead() | _dead_cross_states(), "100.6") == Decision(
        action=Action.SELL, reason=Reason.DEAD_CROSS_EXIT
    )


def test_take_profit_still_applies_with_macd_stop() -> None:
    assert _decide_macd_stop({}, "101.6") == Decision(action=Action.SELL, reason=Reason.TAKE_PROFIT)


def test_rate_stop_is_default() -> None:
    assert _decide_add({}, "96") == Decision(action=Action.SELL, reason=Reason.STOP_LOSS)
