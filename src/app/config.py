import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from src.trading.indicators import IndicatorSettings
from src.trading.signals import SignalSettings, Threshold

MARKET_PREFIX = "KRW-"


class ConfigError(ValueError):
    """설정 파일이 없거나 형식·값이 잘못됨."""


class Timeframe(StrEnum):
    """업비트 분 캔들 단위."""

    MINUTE_1 = "1m"
    MINUTE_3 = "3m"
    MINUTE_5 = "5m"
    MINUTE_10 = "10m"
    MINUTE_15 = "15m"
    MINUTE_30 = "30m"
    MINUTE_60 = "60m"
    MINUTE_240 = "240m"

    @property
    def duration(self) -> timedelta:
        return timedelta(minutes=int(self.value.removesuffix("m")))


@dataclass(frozen=True, kw_only=True)
class OrderSettings:
    """주문 금액 (매매 로직 6.1). 매수 금액 = 원화 주문 가능 잔고 * buy_ratio / 100."""

    buy_ratio: Decimal = Decimal("10.0")


@dataclass(frozen=True, kw_only=True)
class ExitSettings:
    """매도 기준 순수익률(%) (매매 로직 5.3~5.5)."""

    take_profit_rate: Decimal = Decimal("1.5")
    stop_loss_rate: Decimal = Decimal("3.0")
    min_profit_rate: Decimal = Decimal("0.5")


@dataclass(frozen=True, kw_only=True)
class TradingConfig:
    """매매 설정 (매매 로직 2). 항목을 빼면 매매 로직 2.1의 기본값을 쓴다."""

    timeframe: Timeframe
    markets: tuple[str, ...]
    indicator: IndicatorSettings = field(default_factory=IndicatorSettings)
    signal: SignalSettings = field(default_factory=SignalSettings)
    # 신규 매수 시 현재 스토캐스틱 %K, %D가 이 값 이하여야 한다 (매매 로직 5.6)
    stochastic_entry_max: float = 50.0
    order: OrderSettings = field(default_factory=OrderSettings)
    exit: ExitSettings = field(default_factory=ExitSettings)


def load_config(path: Path) -> TradingConfig:
    """설정 파일을 읽고 검증한다. 잘못되면 ConfigError."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(f"cannot read config file: {path}") from error
    except json.JSONDecodeError as error:
        raise ConfigError(f"config file is not valid JSON: {path}") from error
    root = _object(raw, "config", {"timeframe", "markets", "indicator", "threshold", "order", "exit"})
    indicator = _object(root.get("indicator", {}), "indicator", {"ema", "macd_signal", "rsi", "stochastic"})
    threshold = _object(root.get("threshold", {}), "threshold", {"rsi", "stochastic", "stochastic_entry_max"})
    order = _object(root.get("order", {}), "order", {"buy_ratio"})
    exit_ = _object(root.get("exit", {}), "exit", {"take_profit_rate", "stop_loss_rate", "min_profit_rate"})
    # 기간·기준의 범위와 순서는 각 설정 클래스가 만들 때 검증한다
    try:
        return TradingConfig(
            timeframe=_parse_timeframe(root.get("timeframe")),
            markets=_parse_markets(root.get("markets")),
            indicator=_parse_indicator(indicator),
            signal=SignalSettings(
                rsi=_parse_threshold(threshold, "rsi", SignalSettings().rsi),
                stochastic=_parse_threshold(threshold, "stochastic", SignalSettings().stochastic),
            ),
            stochastic_entry_max=_percent(threshold, "stochastic_entry_max", 50.0),
            order=_parse_order(order),
            exit=_parse_exit(exit_),
        )
    except ConfigError:
        raise
    except ValueError as error:
        # 설정 클래스의 검증 오류(ValueError)도 설정 오류로 알린다
        raise ConfigError(str(error)) from error


def _parse_timeframe(value: object) -> Timeframe:
    try:
        return Timeframe(value)
    except ValueError as error:
        allowed = ", ".join(t.value for t in Timeframe)
        raise ConfigError(f"timeframe must be one of {allowed}: {value!r}") from error


def _parse_markets(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError("markets must be a non-empty list")
    for market in value:
        if not isinstance(market, str) or not market.startswith(MARKET_PREFIX) or market == MARKET_PREFIX:
            raise ConfigError(f"market must be a KRW market code like KRW-BTC: {market!r}")
    if len(set(value)) != len(value):
        raise ConfigError("markets must not contain duplicates")
    return tuple(value)


def _object(value: object, name: str, allowed: set[str]) -> Mapping[str, object]:
    """JSON 객체인지, 모르는 키(오타 등)가 없는지 확인한다. 모르는 키가 조용히 기본값으로 바뀌지 않게 한다."""
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a JSON object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ConfigError(f"unknown keys in {name}: {unknown}")
    return value


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ConfigError(f"{name} must be a finite number: {value!r}")
    return float(value)


def _integer(section: Mapping[str, object], key: str, name: str, default: int) -> int:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer: {value!r}")
    return value


def _percent(section: Mapping[str, object], key: str, default: float) -> float:
    value = _number(section.get(key, default), key)
    if not 0 <= value <= 100:
        raise ConfigError(f"{key} must be between 0 and 100: {value}")
    return value


def _decimal(section: Mapping[str, object], key: str, default: Decimal) -> Decimal:
    if key not in section:
        return default
    value = section[key]
    _number(value, key)
    # 문자열을 거쳐 JSON에 적힌 값 그대로 Decimal로 만든다 (Decimal(1.1)은 이진 오차까지 담는다)
    return Decimal(str(value))


def _parse_indicator(section: Mapping[str, object]) -> IndicatorSettings:
    d = IndicatorSettings()
    ema = _object(section.get("ema", {}), "indicator.ema", {"short", "mid", "long"})
    rsi = _object(section.get("rsi", {}), "indicator.rsi", {"period", "signal"})
    stochastic = _object(section.get("stochastic", {}), "indicator.stochastic", {"k_len", "k_smooth", "d_smooth"})
    return IndicatorSettings(
        ema_short=_integer(ema, "short", "indicator.ema.short", d.ema_short),
        ema_mid=_integer(ema, "mid", "indicator.ema.mid", d.ema_mid),
        ema_long=_integer(ema, "long", "indicator.ema.long", d.ema_long),
        macd_signal=_integer(section, "macd_signal", "indicator.macd_signal", d.macd_signal),
        rsi_period=_integer(rsi, "period", "indicator.rsi.period", d.rsi_period),
        rsi_signal=_integer(rsi, "signal", "indicator.rsi.signal", d.rsi_signal),
        stochastic_k_len=_integer(stochastic, "k_len", "indicator.stochastic.k_len", d.stochastic_k_len),
        stochastic_k_smooth=_integer(stochastic, "k_smooth", "indicator.stochastic.k_smooth", d.stochastic_k_smooth),
        stochastic_d_smooth=_integer(stochastic, "d_smooth", "indicator.stochastic.d_smooth", d.stochastic_d_smooth),
    )


def _parse_threshold(section: Mapping[str, object], key: str, default: Threshold) -> Threshold:
    values = _object(section.get(key, {}), f"threshold.{key}", {"over_sold", "over_bought"})
    return Threshold(
        over_sold=_number(values.get("over_sold", default.over_sold), f"threshold.{key}.over_sold"),
        over_bought=_number(values.get("over_bought", default.over_bought), f"threshold.{key}.over_bought"),
    )


def _parse_order(section: Mapping[str, object]) -> OrderSettings:
    buy_ratio = _decimal(section, "buy_ratio", OrderSettings.buy_ratio)
    # 수수료를 더해도 잔고를 넘지 않도록 100보다 작아야 한다 (매매 로직 2.2)
    if not 0 < buy_ratio < 100:
        raise ConfigError(f"order.buy_ratio must be greater than 0 and less than 100: {buy_ratio}")
    return OrderSettings(buy_ratio=buy_ratio)


def _parse_exit(section: Mapping[str, object]) -> ExitSettings:
    d = ExitSettings()
    take_profit = _decimal(section, "take_profit_rate", d.take_profit_rate)
    stop_loss = _decimal(section, "stop_loss_rate", d.stop_loss_rate)
    min_profit = _decimal(section, "min_profit_rate", d.min_profit_rate)
    if take_profit <= 0 or stop_loss <= 0:
        raise ConfigError(f"exit.take_profit_rate and exit.stop_loss_rate must be positive: {take_profit}, {stop_loss}")
    if min_profit < 0:
        raise ConfigError(f"exit.min_profit_rate must not be negative: {min_profit}")
    return ExitSettings(take_profit_rate=take_profit, stop_loss_rate=stop_loss, min_profit_rate=min_profit)
