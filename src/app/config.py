import json
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path

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
class TradingConfig:
    """매매 설정. 지금은 스케줄에 필요한 값만 읽는다."""

    timeframe: Timeframe
    markets: tuple[str, ...]


def load_config(path: Path) -> TradingConfig:
    """설정 파일을 읽고 검증한다. 잘못되면 ConfigError."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(f"cannot read config file: {path}") from error
    except json.JSONDecodeError as error:
        raise ConfigError(f"config file is not valid JSON: {path}") from error
    if not isinstance(raw, dict):
        raise ConfigError("config must be a JSON object")
    return TradingConfig(timeframe=_parse_timeframe(raw.get("timeframe")), markets=_parse_markets(raw.get("markets")))


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
