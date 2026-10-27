import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from src.app.config import ConfigError, ExitSettings, OrderSettings, Timeframe, TradingConfig, load_config
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
    "order": {"buy_ratio": 0.1},
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
    assert config.order == OrderSettings(buy_ratio=Decimal("0.1"))
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
    assert (config.exit.take_profit_rate, config.exit.stop_loss_rate, config.exit.min_profit_rate) == (
        Decimal("1.5"),
        Decimal("3.0"),
        Decimal("0.5"),
    )


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
        (("exit", "take_profit_rate"), 0, "must be positive"),
        (("exit", "stop_loss_rate"), -3, "must be positive"),
        (("exit", "min_profit_rate"), -0.1, "must not be negative"),
    ],
)
def test_invalid_settings_rejected(tmp_path: Path, path: tuple[str, ...], value: object, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_config(_write(tmp_path, _with(path, value)))
