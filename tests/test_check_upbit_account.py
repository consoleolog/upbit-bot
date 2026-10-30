from decimal import Decimal
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from src.check_upbit_account import main
from src.exchange.account import Account, OrderChance
from src.exchange.upbit_client import UpbitAuthError

SECRET = "secret-value-" + "x" * 64


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "config.json"
    path.write_text('{"timeframe": "30m", "markets": ["KRW-BTC"]}', encoding="utf-8")
    return path


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "access-value")
    monkeypatch.setenv("UPBIT_SECRET_KEY", SECRET)


def test_prints_balances_and_order_chance(
    config: Path, keys: None, mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    account = Account(
        currency="KRW", balance=Decimal("10000"), locked=Decimal("0"), avg_buy_price=Decimal("0"), unit_currency="KRW"
    )
    chance = OrderChance(
        market="KRW-BTC",
        bid_fee=Decimal("0.0005"),
        ask_fee=Decimal("0.0005"),
        bid_min_total=Decimal("5000"),
        ask_min_total=Decimal("5000"),
        max_total=Decimal("1000000000"),
        state="active",
    )
    mocker.patch(
        "src.check_upbit_account.UpbitClient.get_accounts", new_callable=mocker.AsyncMock, return_value=[account]
    )
    mocker.patch(
        "src.check_upbit_account.UpbitClient.get_order_chance", new_callable=mocker.AsyncMock, return_value=chance
    )

    assert main(["--config", str(config)]) == 0

    out = capsys.readouterr().out
    assert "KRW: 주문 가능 10000" in out
    assert "KRW-BTC: 매수 수수료 0.0005, 매도 수수료 0.0005, 최소 주문 5000원, 상태 active" in out
    # 키와 토큰은 출력하지 않는다
    assert SECRET not in out and "access-value" not in out


def test_missing_keys_fails(config: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("UPBIT_ACCESS_KEY", raising=False)
    monkeypatch.delenv("UPBIT_SECRET_KEY", raising=False)

    assert main(["--config", str(config)]) == 1
    assert "UPBIT_ACCESS_KEY" in capsys.readouterr().err


def test_upbit_error_fails_without_secret(
    config: Path, keys: None, mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    error = UpbitAuthError(
        "GET /v1/accounts failed (401 no_authorization_ip): ", status=401, name="no_authorization_ip"
    )
    mocker.patch("src.check_upbit_account.UpbitClient.get_accounts", new_callable=mocker.AsyncMock, side_effect=error)

    assert main(["--config", str(config)]) == 1
    err = capsys.readouterr().err
    assert "no_authorization_ip" in err
    assert SECRET not in err
