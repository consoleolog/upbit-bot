from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.app.config import ExitSettings
from src.repositories.models import IndicatorInfo, IndicatorType
from src.trading.decision import Action, Decision, Holding, Reason, decide, net_profit_rate
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


@pytest.mark.parametrize(
    ("states", "reason"),
    [(_overbought_states(), Reason.OVERBOUGHT_EXIT), (_dead_cross_states(), Reason.DEAD_CROSS_EXIT)],
)
def test_signal_sell_above_min_profit(states: dict[IndicatorType, IndicatorInfo], reason: Reason) -> None:
    assert _decide(states, HOLDING, "100.6") == Decision(action=Action.SELL, reason=reason)


@pytest.mark.parametrize("close", ["100.5", "100", "98"])
def test_no_signal_sell_at_or_below_min_profit(close: str) -> None:
    # 순수익률이 최소 순수익률(0.5%)을 넘어야 한다. 손실 상태의 매도는 손절로만 한다
    assert _decide(_overbought_states(), HOLDING, close) is None
    assert _decide(_dead_cross_states(), HOLDING, close) is None


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
