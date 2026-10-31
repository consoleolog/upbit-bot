from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pytest_mock import MockerFixture

from src.check_upbit_account import main
from src.exchange.account import Account, OrderChance
from src.exchange.order import Order, OrderSide, OrderState, OrderType
from src.exchange.upbit_client import UpbitAuthError, UpbitForbiddenError

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


def _chance() -> OrderChance:
    return OrderChance(
        market="KRW-BTC",
        bid_fee=Decimal("0.0005"),
        ask_fee=Decimal("0.0005"),
        bid_min_total=Decimal("5000"),
        ask_min_total=Decimal("5000"),
        max_total=Decimal("1000000000"),
        state="active",
    )


def _test_order() -> Order:
    return Order(
        uuid="cdd92199-2897-4e14-9448-f923320408ad",
        identifier="order-test-1",
        market="KRW-BTC",
        side=OrderSide.BID,
        ord_type=OrderType.PRICE,
        state=OrderState.WAIT,
        created_at=datetime(2026, 10, 31, 17, tzinfo=UTC),
        price=Decimal("5000"),
        volume=None,
        executed_volume=Decimal("0"),
        paid_fee=Decimal("0"),
        trades_count=0,
        trades=(),
    )


@pytest.fixture
def upbit(mocker: MockerFixture) -> dict[str, Any]:
    client = "src.check_upbit_account.UpbitClient"
    mocks = {
        "get_accounts": mocker.patch(f"{client}.get_accounts", new_callable=mocker.AsyncMock, return_value=[]),
        "get_order_chance": mocker.patch(
            f"{client}.get_order_chance", new_callable=mocker.AsyncMock, return_value=_chance()
        ),
        "test_market_buy": mocker.patch(
            f"{client}.test_market_buy", new_callable=mocker.AsyncMock, return_value=_test_order()
        ),
    }
    # 확인 명령은 어떤 경우에도 실제 주문을 보내지 않는다
    for name in ("place_market_buy", "place_market_sell"):
        mocks[name] = mocker.patch(
            f"{client}.{name}", new_callable=mocker.AsyncMock, side_effect=AssertionError("real order")
        )
    return mocks


def test_order_test_validates_minimum_market_buy_without_real_order(
    config: Path, keys: None, upbit: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(config), "--order-test"]) == 0

    market, amount, identifier = upbit["test_market_buy"].await_args.args
    assert (market, amount) == ("KRW-BTC", Decimal("5000"))
    # 업비트 identifier는 다시 쓸 수 없으므로 실행할 때마다 새로 만든다
    assert identifier.startswith("order-test-") and len(identifier) <= 64
    assert upbit["place_market_buy"].await_count == upbit["place_market_sell"].await_count == 0
    assert "KRW-BTC: 주문 생성 테스트 통과 (실제 주문 없음) - 시장가 매수 5000원, 상태 wait" in capsys.readouterr().out


def test_without_order_test_option_only_queries(config: Path, keys: None, upbit: dict[str, Any]) -> None:
    assert main(["--config", str(config)]) == 0

    assert upbit["test_market_buy"].await_count == 0


def test_order_test_without_order_permission_fails(
    config: Path, keys: None, upbit: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    upbit["test_market_buy"].side_effect = UpbitForbiddenError(
        "POST /v1/orders/test failed (401 out_of_scope): 권한이 부족합니다.", status=401, name="out_of_scope"
    )

    assert main(["--config", str(config), "--order-test"]) == 1
    err = capsys.readouterr().err
    assert "out_of_scope" in err
    assert SECRET not in err
