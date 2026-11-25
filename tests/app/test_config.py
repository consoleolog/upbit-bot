import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from src.app.config import (
    AddBuyBase,
    AddBuyTrigger,
    ConfigError,
    ExitSettings,
    OrderSettings,
    StageFilterSettings,
    StopLossBy,
    Timeframe,
    TradingConfig,
    load_config,
)
from src.trading.indicators import IndicatorSettings
from src.trading.signals import SignalSettings, Threshold

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "trading.json"


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_load_valid_config(tmp_path: Path) -> None:
    path = _write(tmp_path, {"timeframe": "30m", "markets": ["KRW-BTC", "KRW-ETH"], "order": {"buy_ratio": 10.0}})

    assert load_config(path) == TradingConfig(timeframe=Timeframe.MINUTE_30, markets=("KRW-BTC", "KRW-ETH"))


@pytest.mark.parametrize(
    ("timeframe", "minutes"),
    [("1m", 1), ("3m", 3), ("5m", 5), ("10m", 10), ("15m", 15), ("30m", 30), ("60m", 60), ("240m", 240)],
)
def test_timeframe_duration(timeframe: str, minutes: int) -> None:
    assert Timeframe(timeframe).duration == timedelta(minutes=minutes)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "missing.json")


def test_directory_instead_of_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path)


def test_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(path)


def test_not_an_object(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="JSON object"):
        load_config(_write(tmp_path, ["KRW-BTC"]))


@pytest.mark.parametrize("timeframe", [None, "", "2m", "30", "1d", 30])
def test_invalid_timeframe(tmp_path: Path, timeframe: object) -> None:
    data: dict[str, object] = {"markets": ["KRW-BTC"]}
    if timeframe is not None:
        data["timeframe"] = timeframe

    with pytest.raises(ConfigError, match="timeframe"):
        load_config(_write(tmp_path, data))


@pytest.mark.parametrize(
    ("markets", "message"),
    [
        (None, "non-empty list"),
        ([], "non-empty list"),
        ("KRW-BTC", "non-empty list"),
        (["BTC-ETH"], "KRW market"),
        (["KRW-"], "KRW market"),
        ([1], "KRW market"),
        (["KRW-BTC", "KRW-BTC"], "duplicates"),
    ],
)
def test_invalid_markets(tmp_path: Path, markets: object, message: str) -> None:
    data: dict[str, object] = {"timeframe": "30m"}
    if markets is not None:
        data["markets"] = markets

    with pytest.raises(ConfigError, match=message):
        load_config(_write(tmp_path, data))


# 매매 로직 2.1의 설정 예시 (기본값과 다른 값으로 바꿔 모든 항목을 읽는지 확인한다)
FULL = {
    "timeframe": "30m",
    "markets": ["KRW-BTC"],
    "indicator": {
        "ema": {"short": 4, "mid": 9, "long": 21},
        "macd_signal": 7,
        "rsi": {"period": 10, "signal": 5},
        "stochastic": {"k_len": 12, "k_smooth": 2, "d_smooth": 3},
    },
    "threshold": {
        "rsi": {"over_sold": 30, "over_bought": 70.5},
        "stochastic": {"over_sold": 20, "over_bought": 80},
        "stochastic_entry_max": 45,
    },
    "order": {"buy_ratio": 0.1, "add_buy_multiple": 2, "max_add_buys": 3},
    "exit": {"take_profit_rate": 2, "stop_loss_rate": 4.5, "min_profit_rate": 0},
}


def test_load_all_sections(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, FULL))

    assert config.indicator == IndicatorSettings(
        ema_short=4,
        ema_mid=9,
        ema_long=21,
        macd_signal=7,
        rsi_period=10,
        rsi_signal=5,
        stochastic_k_len=12,
        stochastic_k_smooth=2,
        stochastic_d_smooth=3,
    )
    assert config.signal == SignalSettings(
        rsi=Threshold(over_sold=30, over_bought=70.5), stochastic=Threshold(over_sold=20, over_bought=80)
    )
    assert config.stochastic_entry_max == 45
    # 비율·수익률은 Decimal로, JSON에 적힌 값 그대로 읽는다 (0.1이 0.1000000000000000055…가 되지 않음)
    assert config.order == OrderSettings(buy_ratio=Decimal("0.1"), add_buy_multiple=Decimal("2"), max_add_buys=3)
    assert str(config.order.buy_ratio) == "0.1"
    assert config.exit == ExitSettings(
        take_profit_rate=Decimal("2"), stop_loss_rate=Decimal("4.5"), min_profit_rate=Decimal("0")
    )


def test_omitted_sections_use_trading_logic_defaults(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, {"timeframe": "30m", "markets": ["KRW-BTC"]}))

    assert config.indicator == IndicatorSettings()
    assert config.signal == SignalSettings()
    assert config.stochastic_entry_max == 50
    assert config.order.buy_ratio == Decimal("10")
    # 추가 매수는 켜지 않으면 하지 않는다 (매매 로직 5.6)
    assert (config.order.add_buy_multiple, config.order.max_add_buys) == (Decimal("1.5"), 0)
    assert (config.exit.take_profit_rate, config.exit.stop_loss_rate, config.exit.min_profit_rate) == (
        Decimal("1.5"),
        Decimal("3.0"),
        Decimal("0.5"),
    )


def test_live_is_off_unless_enabled(tmp_path: Path) -> None:
    assert load_config(_write(tmp_path, {"timeframe": "30m", "markets": ["KRW-BTC"]})).live is False
    assert load_config(_write(tmp_path, {"timeframe": "30m", "markets": ["KRW-BTC"], "live": True})).live is True


@pytest.mark.parametrize("value", ["true", 1, 0, None, "false"])
def test_live_must_be_boolean(tmp_path: Path, value: object) -> None:
    # 실제 주문 여부라서 참·거짓으로 해석될 수 있는 다른 값을 받지 않는다
    with pytest.raises(ConfigError, match="live must be true or false"):
        load_config(_write(tmp_path, {"timeframe": "30m", "markets": ["KRW-BTC"], "live": value}))


def test_repository_config_is_paper() -> None:
    # 실거래는 이 값을 바꾸는 커밋으로만 켠다 (변경 이력을 남긴다). 그 전까지는 모의 실행이다
    assert load_config(REPO_CONFIG).live is False


def test_omitted_keys_inside_section_use_defaults(tmp_path: Path) -> None:
    data = {"timeframe": "30m", "markets": ["KRW-BTC"], "indicator": {"rsi": {"period": 10}}}

    config = load_config(_write(tmp_path, data))

    assert config.indicator.rsi_period == 10
    assert config.indicator.rsi_signal == IndicatorSettings().rsi_signal
    assert config.indicator.ema_long == IndicatorSettings().ema_long


def test_repository_config_is_valid() -> None:
    # 서버에서 쓰는 설정 파일이 항상 읽히는지 확인한다
    assert load_config(REPO_CONFIG).timeframe == Timeframe.MINUTE_30


def _with(path: tuple[str, ...], value: object) -> dict[str, object]:
    data = json.loads(json.dumps(FULL))
    target = data
    for key in path[:-1]:
        target = target.setdefault(key, {})
    target[path[-1]] = value
    return data


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        # 오타 등 모르는 키는 기본값으로 넘어가지 않고 거부한다
        (("indicater",), {}, "unknown keys in config"),
        (("indicator", "ema", "longg"), 21, "unknown keys in indicator.ema"),
        (("threshold", "rsi", "over_bougth"), 70, "unknown keys in threshold.rsi"),
        (("exit", "take_profit"), 1.5, "unknown keys in exit"),
        # 형식
        (("indicator",), [], "indicator must be a JSON object"),
        (("indicator", "rsi", "period"), 14.0, "integer"),
        (("indicator", "rsi", "period"), True, "integer"),
        (("indicator", "macd_signal"), "9", "integer"),
        (("threshold", "rsi", "over_sold"), "31", "finite number"),
        (("threshold", "rsi", "over_sold"), False, "finite number"),
        (("order", "buy_ratio"), float("nan"), "finite number"),
        (("exit", "stop_loss_rate"), float("inf"), "finite number"),
        # 범위와 순서
        (("indicator", "ema", "mid"), 3, "short < mid < long"),
        (("indicator", "stochastic", "k_len"), 0, "positive integers"),
        (("threshold", "stochastic", "over_sold"), 90, "threshold"),
        (("threshold", "stochastic_entry_max"), 101, "between 0 and 100"),
        (("order", "buy_ratio"), 0, "buy_ratio"),
        (("order", "buy_ratio"), 100, "buy_ratio"),
        (("order", "add_buy_multiple"), 0, "add_buy_multiple must be positive"),
        (("order", "add_buy_multiple"), float("inf"), "finite number"),
        (("order", "max_add_buys"), -1, "max_add_buys must not be negative"),
        (("order", "max_add_buys"), 1.5, "integer"),
        (("order", "max_add_buys"), True, "integer"),
        (("exit", "take_profit_rate"), 0, "must be positive"),
        (("exit", "stop_loss_rate"), -3, "must be positive"),
        (("exit", "min_profit_rate"), -0.1, "must not be negative"),
    ],
)
def test_invalid_settings_rejected(tmp_path: Path, path: tuple[str, ...], value: object, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_config(_write(tmp_path, _with(path, value)))


@pytest.mark.parametrize(("buy_count", "units"), [(1, "1.5"), (2, "2.25"), (3, "3.375")])
def test_add_buy_units_grow_from_previous_buy(buy_count: int, units: str) -> None:
    # 직전 매수 기준이면 직전 매수 금액의 add_buy_multiple배다
    settings = OrderSettings(add_buy_multiple=Decimal("1.5"), add_buy_base=AddBuyBase.PREVIOUS)
    assert settings.add_buy_units(buy_count) == Decimal(units)


@pytest.mark.parametrize("buy_count", [1, 2, 3])
def test_add_buy_units_from_first_buy_by_default(buy_count: int) -> None:
    # 기본은 매번 첫 매수 금액의 add_buy_multiple배다
    assert OrderSettings(add_buy_multiple=Decimal("1.5")).add_buy_units(buy_count) == Decimal("1.5")


@pytest.mark.parametrize(("value", "expected"), [("first", AddBuyBase.FIRST), ("previous", AddBuyBase.PREVIOUS)])
def test_add_buy_base_is_read(tmp_path: Path, value: str, expected: AddBuyBase) -> None:
    config = load_config(_write(tmp_path, _with(("order", "add_buy_base"), value)))

    assert config.order.add_buy_base is expected


@pytest.mark.parametrize("value", ["last", "", 1, None])
def test_invalid_add_buy_base_rejected(tmp_path: Path, value: object) -> None:
    with pytest.raises(ConfigError, match=r"order\.add_buy_base"):
        load_config(_write(tmp_path, _with(("order", "add_buy_base"), value)))


def test_add_buy_units_needs_a_buy() -> None:
    with pytest.raises(ValueError, match="buy_count"):
        OrderSettings().add_buy_units(0)


def test_repository_config_order() -> None:
    # 매수는 원화 잔고의 20%, 추가 매수는 첫 매수 금액의 1.5배로 최대 2번
    order = load_config(REPO_CONFIG).order
    assert (order.buy_ratio, order.add_buy_multiple, order.max_add_buys, order.add_buy_base) == (
        Decimal("20"),
        Decimal("1.5"),
        2,
        AddBuyBase.FIRST,
    )


PAPER_CONFIG = REPO_CONFIG.with_name("trading.paper.json")


def test_paper_config_is_paper_with_more_markets_previous_add_buy_and_15m() -> None:
    paper = load_config(PAPER_CONFIG)

    # 모의 실행 봇의 설정은 실거래로 바뀌지 않는다 (prod에서도 모의 실행)
    assert paper.live is False
    assert paper.markets == ("KRW-BTC", "KRW-ETH", "KRW-BCH", "KRW-SOL", "KRW-AAVE", "KRW-BSV", "KRW-ENS")
    assert paper.order.add_buy_base is AddBuyBase.PREVIOUS
    assert paper.timeframe is Timeframe.MINUTE_15
    assert paper.order.add_buy_trigger is AddBuyTrigger.LOSS_RELAXED
    assert paper.order.stop_loss_after_adds is True


def test_paper_config_differs_from_live_only_in_markets_add_buy_and_timeframe() -> None:
    paper, live = load_config(PAPER_CONFIG), load_config(REPO_CONFIG)

    # 마켓, 추가 매수 기준, 캔들 주기만 다르다. 그 밖의 매매 기준은 실거래와 같아야 비교 시험이 된다
    assert (
        replace(
            paper,
            markets=live.markets,
            live=live.live,
            timeframe=live.timeframe,
            order=replace(paper.order, add_buy_base=live.order.add_buy_base),
        )
        == live
    )


@pytest.mark.parametrize(
    ("value", "expected"), [("entry", AddBuyTrigger.ENTRY), ("loss_relaxed", AddBuyTrigger.LOSS_RELAXED)]
)
def test_add_buy_trigger_is_read(tmp_path: Path, value: str, expected: AddBuyTrigger) -> None:
    config = load_config(_write(tmp_path, _with(("order", "add_buy_trigger"), value)))

    assert config.order.add_buy_trigger is expected


@pytest.mark.parametrize("value", ["loss", "", 1, None])
def test_invalid_add_buy_trigger_rejected(tmp_path: Path, value: object) -> None:
    with pytest.raises(ConfigError, match=r"order\.add_buy_trigger"):
        load_config(_write(tmp_path, _with(("order", "add_buy_trigger"), value)))


@pytest.mark.parametrize("value", [True, False])
def test_stop_loss_after_adds_is_read(tmp_path: Path, value: bool) -> None:
    config = load_config(_write(tmp_path, _with(("order", "stop_loss_after_adds"), value)))

    assert config.order.stop_loss_after_adds is value


@pytest.mark.parametrize("value", ["true", 1, None])
def test_invalid_stop_loss_after_adds_rejected(tmp_path: Path, value: object) -> None:
    with pytest.raises(ConfigError, match=r"order\.stop_loss_after_adds"):
        load_config(_write(tmp_path, _with(("order", "stop_loss_after_adds"), value)))


def test_loss_recovery_off_by_default() -> None:
    order = OrderSettings()
    assert (order.add_buy_trigger, order.stop_loss_after_adds, order.stop_loss_by) == (
        AddBuyTrigger.ENTRY,
        False,
        StopLossBy.RATE,
    )


@pytest.mark.parametrize("path", [REPO_CONFIG, REPO_CONFIG.with_name("trading.paper.json")], ids=["live", "paper"])
def test_repository_configs_use_loss_recovery_and_macd_stop(path: Path) -> None:
    # 손실 중 완화된 조건으로 추가 매수하고, 추가 매수를 다 쓴 뒤 MACD 3종 데드크로스로 손절한다 (고정 % 손절 없음)
    order = load_config(path).order
    assert (order.add_buy_trigger, order.stop_loss_after_adds, order.stop_loss_by) == (
        AddBuyTrigger.LOSS_RELAXED,
        True,
        StopLossBy.MACD_DEAD_CROSS,
    )


@pytest.mark.parametrize(("buy_count", "adds_left", "deferred"), [(1, True, True), (2, True, True), (3, False, False)])
def test_stop_loss_deferred_only_while_adds_left(buy_count: int, adds_left: bool, deferred: bool) -> None:
    order = OrderSettings(max_add_buys=2, stop_loss_after_adds=True)

    assert order.adds_left(buy_count) is adds_left
    assert order.stop_loss_deferred(buy_count) is deferred
    # 설정을 끄면 미루지 않는다
    assert replace(order, stop_loss_after_adds=False).stop_loss_deferred(buy_count) is False


@pytest.mark.parametrize(
    ("value", "expected"), [("rate", StopLossBy.RATE), ("macd_dead_cross", StopLossBy.MACD_DEAD_CROSS)]
)
def test_stop_loss_by_is_read(tmp_path: Path, value: str, expected: StopLossBy) -> None:
    config = load_config(_write(tmp_path, _with(("order", "stop_loss_by"), value)))

    assert config.order.stop_loss_by is expected


@pytest.mark.parametrize("value", ["macd", "", 1, None])
def test_invalid_stop_loss_by_rejected(tmp_path: Path, value: object) -> None:
    with pytest.raises(ConfigError, match=r"order\.stop_loss_by"):
        load_config(_write(tmp_path, _with(("order", "stop_loss_by"), value)))


# ---------------------------------------------------------------------------
# 신규 매수 스테이지 필터
# ---------------------------------------------------------------------------

STAGE_FILTER = {"ema": {"short": 20, "mid": 50, "long": 100}, "block_stages": [4]}


def test_stage_filter_off_by_default(tmp_path: Path) -> None:
    assert load_config(_write(tmp_path, FULL)).stage_filter is None


def test_stage_filter_is_read(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, _with(("stage_filter",), STAGE_FILTER)))

    assert config.stage_filter == StageFilterSettings(
        ema_short=20, ema_mid=50, ema_long=100, block_stages=frozenset({4})
    )


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([], "stage_filter must be a JSON object"),
        ({"ema": {"short": 20, "mid": 50, "long": 100}}, "must have ema and block_stages"),
        ({"block_stages": [4]}, "must have ema and block_stages"),
        (STAGE_FILTER | {"block": [4]}, "unknown keys in stage_filter"),
        (STAGE_FILTER | {"ema": {"short": 20, "mid": 50}}, "must have short, mid and long"),
        (STAGE_FILTER | {"ema": {"short": 20, "mid": 50, "long": 100.0}}, "integer"),
        (STAGE_FILTER | {"ema": {"short": 50, "mid": 20, "long": 100}}, "short < mid < long"),
        (STAGE_FILTER | {"ema": {"short": 0, "mid": 50, "long": 100}}, "positive integers"),
        (STAGE_FILTER | {"block_stages": []}, "non-empty subset"),
        (STAGE_FILTER | {"block_stages": [7]}, "non-empty subset"),
        (STAGE_FILTER | {"block_stages": 4}, "list of integers"),
        (STAGE_FILTER | {"block_stages": [True]}, "list of integers"),
        (STAGE_FILTER | {"block_stages": ["4"]}, "list of integers"),
    ],
)
def test_invalid_stage_filter_rejected(tmp_path: Path, value: object, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_config(_write(tmp_path, _with(("stage_filter",), value)))


@pytest.mark.parametrize(("stage", "allowed"), [(None, True), (0, True), (1, True), (3, True), (4, False), (5, True)])
def test_stage_filter_blocks_only_listed_stages(stage: int | None, allowed: bool) -> None:
    settings = StageFilterSettings(ema_short=20, ema_mid=50, ema_long=100, block_stages=frozenset({4}))

    assert settings.allows(stage) is allowed


@pytest.mark.parametrize("path", [REPO_CONFIG, PAPER_CONFIG], ids=["live", "paper"])
def test_repository_configs_block_entry_in_long_downtrend(path: Path) -> None:
    # 30분봉 EMA 20/50/100이 장 > 중 > 단(스테이지 4, 안정적인 하락)이면 신규 매수하지 않는다
    assert load_config(path).stage_filter == StageFilterSettings(
        ema_short=20, ema_mid=50, ema_long=100, block_stages=frozenset({4})
    )
