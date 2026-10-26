import json
from datetime import timedelta
from pathlib import Path

import pytest

from src.config import ConfigError, Timeframe, TradingConfig, load_config


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
