from decimal import Decimal

import pytest

from src.models.asset import Asset

SAMPLE_DICT = {
    "currency": "BTC",
    "balance": "0.5",
    "locked": "0.1",
    "avg_buy_price": "95000000",
    "avg_buy_price_modified": False,
    "unit_currency": "KRW",
}

DECIMAL_FIELDS = ["balance", "locked", "avg_buy_price"]


# ---------------------------------------------------------------------------
# from_dict — 기본 필드
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_from_dict_currency():
    asset = Asset.from_dict(SAMPLE_DICT)
    assert asset.currency == "BTC"


@pytest.mark.unit
def test_from_dict_unit_currency():
    asset = Asset.from_dict(SAMPLE_DICT)
    assert asset.unit_currency == "KRW"


@pytest.mark.unit
def test_from_dict_avg_buy_price_modified():
    asset = Asset.from_dict(SAMPLE_DICT)
    assert asset.avg_buy_price_modified is False


@pytest.mark.unit
@pytest.mark.parametrize("field", DECIMAL_FIELDS)
def test_from_dict_decimal_fields_are_decimal(field: str):
    """문자열로 전달된 Decimal 필드가 Decimal 타입으로 변환된다."""
    asset = Asset.from_dict(SAMPLE_DICT)
    assert isinstance(getattr(asset, field), Decimal)


@pytest.mark.unit
def test_from_dict_balance_value():
    asset = Asset.from_dict(SAMPLE_DICT)
    assert asset.balance == Decimal("0.5")


@pytest.mark.unit
def test_from_dict_locked_value():
    asset = Asset.from_dict(SAMPLE_DICT)
    assert asset.locked == Decimal("0.1")


@pytest.mark.unit
def test_from_dict_avg_buy_price_value():
    asset = Asset.from_dict(SAMPLE_DICT)
    assert asset.avg_buy_price == Decimal("95000000")


# ---------------------------------------------------------------------------
# __post_init__ — 타입 변환
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("field", DECIMAL_FIELDS)
def test_post_init_converts_str_to_decimal(field: str):
    """생성자에 문자열을 전달하면 __post_init__이 Decimal로 변환한다."""
    kwargs = {
        "currency": "KRW",
        "balance": "1000000",
        "locked": "0",
        "avg_buy_price": "0",
        "avg_buy_price_modified": False,
        "unit_currency": "KRW",
    }
    asset = Asset(**kwargs)
    assert isinstance(getattr(asset, field), Decimal)


@pytest.mark.unit
@pytest.mark.parametrize("field", DECIMAL_FIELDS)
def test_post_init_converts_int_to_decimal(field: str):
    """생성자에 int를 전달하면 __post_init__이 Decimal로 변환한다."""
    asset = Asset(
        currency="KRW", balance=0, locked=0, avg_buy_price=0, avg_buy_price_modified=False, unit_currency="KRW"
    )
    assert isinstance(getattr(asset, field), Decimal)


@pytest.mark.unit
@pytest.mark.parametrize("field", DECIMAL_FIELDS)
def test_post_init_preserves_decimal(field: str):
    """이미 Decimal인 값은 그대로 유지된다."""
    asset = Asset(
        currency="KRW",
        balance=Decimal("1000"),
        locked=Decimal("0"),
        avg_buy_price=Decimal("0"),
        avg_buy_price_modified=False,
        unit_currency="KRW",
    )
    assert isinstance(getattr(asset, field), Decimal)


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_to_dict_returns_dict():
    asset = Asset.from_dict(SAMPLE_DICT)
    assert isinstance(asset.to_dict(), dict)


@pytest.mark.unit
def test_to_dict_currency():
    asset = Asset.from_dict(SAMPLE_DICT)
    assert asset.to_dict()["currency"] == "BTC"


@pytest.mark.unit
def test_to_dict_unit_currency():
    asset = Asset.from_dict(SAMPLE_DICT)
    assert asset.to_dict()["unit_currency"] == "KRW"


@pytest.mark.unit
def test_to_dict_avg_buy_price_modified():
    asset = Asset.from_dict(SAMPLE_DICT)
    assert asset.to_dict()["avg_buy_price_modified"] is False


@pytest.mark.unit
@pytest.mark.parametrize("field", DECIMAL_FIELDS)
def test_to_dict_decimal_fields_present(field: str):
    """to_dict()에 모든 Decimal 필드가 포함된다."""
    result = Asset.from_dict(SAMPLE_DICT).to_dict()
    assert field in result


@pytest.mark.unit
def test_to_dict_contains_all_keys():
    expected_keys = {"currency", "balance", "locked", "avg_buy_price", "avg_buy_price_modified", "unit_currency"}
    result = Asset.from_dict(SAMPLE_DICT).to_dict()
    assert set(result.keys()) == expected_keys


# ---------------------------------------------------------------------------
# 왕복 변환 (roundtrip)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_roundtrip_from_dict_to_dict():
    """from_dict → to_dict 후 원본 값과 동일해야 한다."""
    asset = Asset.from_dict(SAMPLE_DICT)
    result = asset.to_dict()

    assert result["currency"] == SAMPLE_DICT["currency"]
    assert result["avg_buy_price_modified"] == SAMPLE_DICT["avg_buy_price_modified"]
    assert result["unit_currency"] == SAMPLE_DICT["unit_currency"]
    assert Decimal(str(result["balance"])) == Decimal(SAMPLE_DICT["balance"])
    assert Decimal(str(result["locked"])) == Decimal(SAMPLE_DICT["locked"])
    assert Decimal(str(result["avg_buy_price"])) == Decimal(SAMPLE_DICT["avg_buy_price"])


# ---------------------------------------------------------------------------
# KRW 자산
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_from_dict_krw_asset():
    """KRW 법정 통화 자산도 올바르게 파싱된다."""
    data = {
        "currency": "KRW",
        "balance": "1500000.0",
        "locked": "500000.0",
        "avg_buy_price": "0",
        "avg_buy_price_modified": False,
        "unit_currency": "KRW",
    }
    asset = Asset.from_dict(data)
    assert asset.currency == "KRW"
    assert asset.balance == Decimal("1500000.0")
    assert asset.locked == Decimal("500000.0")
