import hashlib
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import unquote, urlencode

import jwt
import pytest

from src.connections.adapter import UpbitAdapter
from src.models import Candle, Order
from src.models.asset import Asset
from src.models.ticker import Ticker
from src.utils.constants import (
    AskBid,
    CandleType,
    ChangeDirection,
    MarketState,
    OrderSide,
    OrderType,
    SmpType,
    StreamType,
    Timeframe,
    TimeInForce,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> dict:
    return {"api_key": "test-key", "api_secret": "test-secret-that-is-long-enough-32b", "is_test": False}


@pytest.fixture
def adapter(config: dict) -> UpbitAdapter:
    return UpbitAdapter(config)


def _make_session(closed: bool = False) -> MagicMock:
    """aiohttp.ClientSession 모의 객체를 생성한다."""
    session = MagicMock()
    session.closed = closed
    session.close = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_init_base_url(adapter: UpbitAdapter):
    assert adapter.base_url == "https://api.upbit.com/v1"


@pytest.mark.unit
def test_init_credentials(adapter: UpbitAdapter):
    assert adapter.api_key == "test-key"
    assert adapter.api_secret == "test-secret-that-is-long-enough-32b"


@pytest.mark.unit
def test_init_is_test_false(adapter: UpbitAdapter):
    assert adapter.is_test is False


@pytest.mark.unit
def test_init_is_test_true(config: dict):
    config["is_test"] = True
    a = UpbitAdapter(config)
    assert a.is_test is True


@pytest.mark.unit
def test_init_session_is_none(adapter: UpbitAdapter):
    assert adapter._session is None


@pytest.mark.unit
def test_init_missing_keys_use_defaults():
    """config에 키가 없으면 기본값을 사용한다."""
    a = UpbitAdapter({})
    assert a.api_key == ""
    assert a.api_secret == ""
    assert a.is_test is False


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_connect_creates_session(adapter: UpbitAdapter):
    """세션이 없을 때 connect()는 새 ClientSession을 생성한다."""
    with patch("src.connections.adapter.aiohttp.ClientSession") as mock_cls:
        mock_cls.return_value = _make_session()
        await adapter.connect()

    assert adapter._session is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_connect_does_not_recreate_open_session(adapter: UpbitAdapter):
    """세션이 이미 열려있으면 새로 생성하지 않는다."""
    existing = _make_session(closed=False)
    adapter._session = existing

    with patch("src.connections.adapter.aiohttp.ClientSession") as mock_cls:
        await adapter.connect()
        mock_cls.assert_not_called()

    assert adapter._session is existing


@pytest.mark.unit
@pytest.mark.asyncio
async def test_connect_recreates_closed_session(adapter: UpbitAdapter):
    """세션이 닫혀있으면 새 세션을 생성한다."""
    adapter._session = _make_session(closed=True)
    new_session = _make_session(closed=False)

    with patch("src.connections.adapter.aiohttp.ClientSession", return_value=new_session):
        await adapter.connect()

    assert adapter._session is new_session


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_disconnect_closes_open_session(adapter: UpbitAdapter):
    """열린 세션을 닫고 None으로 초기화한다."""
    session = _make_session(closed=False)
    adapter._session = session

    await adapter.disconnect()

    session.close.assert_awaited_once()
    assert adapter._session is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_disconnect_does_nothing_when_session_is_none(adapter: UpbitAdapter):
    """세션이 None이면 아무것도 하지 않는다."""
    await adapter.disconnect()  # 예외 없이 통과


@pytest.mark.unit
@pytest.mark.asyncio
async def test_disconnect_does_nothing_when_session_already_closed(adapter: UpbitAdapter):
    """이미 닫힌 세션에 대해 close()를 호출하지 않는다."""
    session = _make_session(closed=True)
    adapter._session = session

    await adapter.disconnect()

    session.close.assert_not_awaited()


# ---------------------------------------------------------------------------
# _ensure_session
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ensure_session_calls_connect_when_no_session(adapter: UpbitAdapter):
    """세션이 없으면 connect()를 호출한다."""
    with patch("src.connections.adapter.aiohttp.ClientSession") as mock_cls:
        mock_cls.return_value = _make_session()
        await adapter._ensure_session()

    assert adapter._session is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ensure_session_does_not_reconnect_when_open(adapter: UpbitAdapter):
    """세션이 열려있으면 connect()를 호출하지 않는다."""
    adapter._session = _make_session(closed=False)

    with patch.object(adapter, "connect", new_callable=AsyncMock) as mock_connect:
        await adapter._ensure_session()
        mock_connect.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ensure_session_reconnects_when_closed(adapter: UpbitAdapter):
    """세션이 닫혀있으면 connect()를 호출한다."""
    adapter._session = _make_session(closed=True)

    with patch.object(adapter, "connect", new_callable=AsyncMock) as mock_connect:
        await adapter._ensure_session()
        mock_connect.assert_called_once()


# ---------------------------------------------------------------------------
# _sign_request
# ---------------------------------------------------------------------------


def _decode(token: str, secret: str) -> dict:
    """JWT 토큰을 검증 없이 디코딩한다."""
    return jwt.decode(token, secret, algorithms=["HS256"])


@pytest.mark.unit
def test_sign_request_returns_string(adapter: UpbitAdapter):
    """반환값이 문자열이다."""
    assert isinstance(adapter._sign_request(), str)


@pytest.mark.unit
def test_sign_request_is_valid_jwt(adapter: UpbitAdapter):
    """반환된 토큰이 api_secret으로 검증 가능하다."""
    token = adapter._sign_request()
    payload = _decode(token, adapter.api_secret)
    assert payload is not None


@pytest.mark.unit
def test_sign_request_payload_contains_access_key(adapter: UpbitAdapter):
    """페이로드에 api_key가 access_key로 포함된다."""
    token = adapter._sign_request()
    payload = _decode(token, adapter.api_secret)
    assert payload["access_key"] == adapter.api_key


@pytest.mark.unit
def test_sign_request_payload_contains_nonce(adapter: UpbitAdapter):
    """페이로드에 nonce(UUID 문자열)가 포함된다."""
    import uuid

    token = adapter._sign_request()
    payload = _decode(token, adapter.api_secret)
    nonce = payload["nonce"]
    assert uuid.UUID(nonce)  # 유효한 UUID 형식


@pytest.mark.unit
def test_sign_request_nonce_is_unique(adapter: UpbitAdapter):
    """호출마다 고유한 nonce가 생성된다."""
    token_a = adapter._sign_request()
    token_b = adapter._sign_request()
    nonce_a = _decode(token_a, adapter.api_secret)["nonce"]
    nonce_b = _decode(token_b, adapter.api_secret)["nonce"]
    assert nonce_a != nonce_b


@pytest.mark.unit
def test_sign_request_without_params_has_no_query_hash(adapter: UpbitAdapter):
    """params 없이 호출하면 query_hash가 포함되지 않는다."""
    token = adapter._sign_request()
    payload = _decode(token, adapter.api_secret)
    assert "query_hash" not in payload
    assert "query_hash_alg" not in payload


@pytest.mark.unit
def test_sign_request_with_params_includes_query_hash(adapter: UpbitAdapter):
    """params 전달 시 query_hash와 query_hash_alg가 포함된다."""
    token = adapter._sign_request({"market": "KRW-BTC"})
    payload = _decode(token, adapter.api_secret)
    assert "query_hash" in payload
    assert payload["query_hash_alg"] == "SHA512"


@pytest.mark.unit
def test_sign_request_query_hash_is_correct(adapter: UpbitAdapter):
    """query_hash가 params의 SHA512 해시와 일치한다."""
    params = {"market": "KRW-BTC", "side": "bid"}
    query_string = unquote(urlencode(params, doseq=True)).encode("utf-8")
    expected_hash = hashlib.sha512(query_string).hexdigest()

    token = adapter._sign_request(params)
    payload = _decode(token, adapter.api_secret)
    assert payload["query_hash"] == expected_hash


@pytest.mark.unit
def test_sign_request_post_omits_query_hash(adapter: UpbitAdapter):
    """POST 요청 시나리오(params=None)에서는 query_hash가 포함되지 않는다."""
    token = adapter._sign_request(None)
    payload = _decode(token, adapter.api_secret)
    assert "query_hash" not in payload


@pytest.mark.unit
def test_sign_request_with_empty_params_has_no_query_hash(adapter: UpbitAdapter):
    """빈 dict 전달 시 query_hash가 포함되지 않는다."""
    token = adapter._sign_request({})
    payload = _decode(token, adapter.api_secret)
    assert "query_hash" not in payload


@pytest.mark.unit
def test_sign_request_wrong_secret_raises(adapter: UpbitAdapter):
    """잘못된 secret으로 디코딩하면 예외가 발생한다."""
    token = adapter._sign_request()
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, "wrong-secret-that-is-long-enough-32b", algorithms=["HS256"])


# ---------------------------------------------------------------------------
# _request
# ---------------------------------------------------------------------------


def _make_api_response(data: object = None, status: int = 200) -> MagicMock:
    """error_handler가 처리할 수 있는 aiohttp 응답 모의 객체를 생성한다."""
    response = MagicMock()
    response.status = status
    response.json = AsyncMock(return_value=data if data is not None else {"ok": True})
    response.text = AsyncMock(return_value="")
    response.release = MagicMock()
    return response


@pytest.fixture
def mock_session() -> MagicMock:
    session = MagicMock()
    session.closed = False
    return session


@pytest.fixture
def connected_adapter(adapter: UpbitAdapter, mock_session: MagicMock) -> UpbitAdapter:
    """세션이 연결된 상태의 adapter."""
    adapter._session = mock_session
    return adapter


# GET


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_get_calls_session_get(connected_adapter: UpbitAdapter, mock_session: MagicMock):
    """GET 요청은 session.get()을 올바른 URL로 호출한다."""
    response = _make_api_response()
    mock_session.get = AsyncMock(return_value=response)

    await connected_adapter._request("GET", "/markets")

    mock_session.get.assert_awaited_once()
    call_kwargs = mock_session.get.call_args
    assert call_kwargs.args[0] == "https://api.upbit.com/v1/markets"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_get_passes_params(connected_adapter: UpbitAdapter, mock_session: MagicMock):
    """GET 요청은 params를 쿼리 파라미터로 전달한다."""
    response = _make_api_response()
    mock_session.get = AsyncMock(return_value=response)

    await connected_adapter._request("GET", "/candles", params={"market": "KRW-BTC"})

    _, kwargs = mock_session.get.call_args
    assert kwargs["params"] == {"market": "KRW-BTC"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_get_returns_json_data(connected_adapter: UpbitAdapter, mock_session: MagicMock):
    """GET 요청의 반환값은 error_handler가 파싱한 JSON 데이터다."""
    data = [{"market": "KRW-BTC"}, {"market": "KRW-ETH"}]
    mock_session.get = AsyncMock(return_value=_make_api_response(data))

    result = await connected_adapter._request("GET", "/markets")

    assert result == data


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_post_signed_passes_params_to_sign_request(
    connected_adapter: UpbitAdapter, mock_session: MagicMock
):
    """POST 요청 시 signed=True이면 params가 _sign_request에 전달된다."""
    response = _make_api_response()
    mock_session.post = AsyncMock(return_value=response)
    params = {"market": "KRW-BTC", "side": "bid", "price": "10000"}

    with patch.object(connected_adapter, "_sign_request", wraps=connected_adapter._sign_request) as mock_sign:
        await connected_adapter._request("POST", "/orders", params=params, signed=True)
        mock_sign.assert_called_with(params)


# POST


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_post_calls_session_post(connected_adapter: UpbitAdapter, mock_session: MagicMock):
    """POST 요청은 session.post()를 올바른 URL로 호출한다."""
    response = _make_api_response()
    mock_session.post = AsyncMock(return_value=response)

    await connected_adapter._request("POST", "/orders")

    mock_session.post.assert_awaited_once()
    call_kwargs = mock_session.post.call_args
    assert call_kwargs.args[0] == "https://api.upbit.com/v1/orders"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_post_passes_params_as_json(connected_adapter: UpbitAdapter, mock_session: MagicMock):
    """POST 요청은 params를 json body로 전달한다."""
    response = _make_api_response()
    mock_session.post = AsyncMock(return_value=response)
    body = {"market": "KRW-BTC", "side": "bid", "volume": "0.001"}

    await connected_adapter._request("POST", "/orders", params=body)

    _, kwargs = mock_session.post.call_args
    assert kwargs["json"] == body


# DELETE


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_delete_calls_session_delete(connected_adapter: UpbitAdapter, mock_session: MagicMock):
    """DELETE 요청은 session.delete()를 올바른 URL로 호출한다."""
    response = _make_api_response()
    mock_session.delete = AsyncMock(return_value=response)

    await connected_adapter._request("DELETE", "/order", params={"uuid": "abc-123"})

    mock_session.delete.assert_awaited_once()
    call_kwargs = mock_session.delete.call_args
    assert call_kwargs.args[0] == "https://api.upbit.com/v1/order"


# 알 수 없는 메서드


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_unknown_method_raises_value_error(connected_adapter: UpbitAdapter):
    """정의되지 않은 HTTP 메서드는 ValueError를 발생시킨다."""
    with pytest.raises(ValueError, match="Unknown method"):
        await connected_adapter._request("PATCH", "/something")


# signed


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_signed_adds_authorization_header(connected_adapter: UpbitAdapter, mock_session: MagicMock):
    """signed=True이면 Authorization 헤더가 추가된다."""
    response = _make_api_response()
    mock_session.get = AsyncMock(return_value=response)

    await connected_adapter._request("GET", "/accounts", signed=True)

    _, kwargs = mock_session.get.call_args
    assert "Authorization" in kwargs["headers"]
    assert kwargs["headers"]["Authorization"].startswith("Bearer ")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_unsigned_has_no_authorization_header(connected_adapter: UpbitAdapter, mock_session: MagicMock):
    """signed=False이면 Authorization 헤더가 없다."""
    response = _make_api_response()
    mock_session.get = AsyncMock(return_value=response)

    await connected_adapter._request("GET", "/markets", signed=False)

    _, kwargs = mock_session.get.call_args
    assert "Authorization" not in kwargs["headers"]


# 기본값


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_none_params_defaults_to_empty_dict(connected_adapter: UpbitAdapter, mock_session: MagicMock):
    """params=None이면 빈 dict로 처리된다."""
    response = _make_api_response()
    mock_session.get = AsyncMock(return_value=response)

    await connected_adapter._request("GET", "/markets", params=None)

    _, kwargs = mock_session.get.call_args
    assert kwargs["params"] == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_none_headers_defaults_to_empty_dict(connected_adapter: UpbitAdapter, mock_session: MagicMock):
    """headers=None이면 빈 dict로 처리된다."""
    response = _make_api_response()
    mock_session.get = AsyncMock(return_value=response)

    await connected_adapter._request("GET", "/markets", headers=None)

    _, kwargs = mock_session.get.call_args
    assert isinstance(kwargs["headers"], dict)


# ClientError


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_client_error_is_reraised(connected_adapter: UpbitAdapter, mock_session: MagicMock):
    """aiohttp.ClientError 발생 시 예외가 그대로 전파된다."""
    import aiohttp

    mock_session.get = AsyncMock(side_effect=aiohttp.ClientError("연결 실패"))

    with pytest.raises(aiohttp.ClientError):
        await connected_adapter._request("GET", "/markets")


# _ensure_session 호출 확인


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_calls_ensure_session(adapter: UpbitAdapter):
    """_request()는 항상 _ensure_session()을 호출한다."""
    response = _make_api_response()

    with (
        patch.object(adapter, "_ensure_session", new_callable=AsyncMock) as mock_ensure,
        patch.object(adapter, "_session") as mock_session,
    ):
        mock_session.closed = False
        mock_session.get = AsyncMock(return_value=response)
        await adapter._request("GET", "/markets")

    mock_ensure.assert_awaited_once()


# ---------------------------------------------------------------------------
# get_candles / _timeframe_to_candle_type
# ---------------------------------------------------------------------------

_SAMPLE_RAW = [
    {
        "market": "KRW-BTC",
        "candle_date_time_utc": "2025-01-03T00:00:00",
        "candle_date_time_kst": "2025-01-03T09:00:00",
        "opening_price": 143000000.0,
        "high_price": 145000000.0,
        "low_price": 142000000.0,
        "trade_price": 144000000.0,
        "candle_acc_trade_volume": 10.5,
        "candle_acc_trade_price": 1500000000.0,
        "timestamp": 1735862400000,
    },
    {
        "market": "KRW-BTC",
        "candle_date_time_utc": "2025-01-02T00:00:00",
        "candle_date_time_kst": "2025-01-02T09:00:00",
        "opening_price": 141000000.0,
        "high_price": 143000000.0,
        "low_price": 140000000.0,
        "trade_price": 142000000.0,
        "candle_acc_trade_volume": 8.2,
        "candle_acc_trade_price": 1160000000.0,
        "timestamp": 1735776000000,
    },
]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_candles_returns_list_of_candles(adapter: UpbitAdapter):
    """반환값이 Candle 리스트다."""
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_RAW):
        result = await adapter.get_candles("KRW-BTC")

    assert isinstance(result, list)
    assert all(isinstance(c, Candle) for c in result)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_candles_reverses_order(adapter: UpbitAdapter):
    """API 응답(최신→과거)을 과거→최신 순으로 역전한다."""
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_RAW):
        result = await adapter.get_candles("KRW-BTC")

    # _SAMPLE_RAW[0]이 최신, [1]이 과거 → 역전 후 [0]이 과거여야 함
    assert result[0].candle_date_time_utc < result[1].candle_date_time_utc


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_candles_calls_request_with_correct_endpoint(adapter: UpbitAdapter):
    """_request()가 올바른 엔드포인트로 호출된다."""
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_RAW) as mock_req:
        await adapter.get_candles("KRW-BTC", timeframe=Timeframe.MINUTE_1)

    mock_req.assert_awaited_once()
    assert mock_req.call_args.args[1] == f"/candles/{Timeframe.MINUTE_1.value}"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_candles_passes_market_and_count(adapter: UpbitAdapter):
    """market과 count가 params에 포함된다."""
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_RAW) as mock_req:
        await adapter.get_candles("KRW-ETH", count=50)

    params = mock_req.call_args.kwargs["params"]
    assert params["market"] == "KRW-ETH"
    assert params["count"] == 50


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_candles_default_count_is_200(adapter: UpbitAdapter):
    """count 기본값은 200이다."""
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_RAW) as mock_req:
        await adapter.get_candles("KRW-BTC")

    params = mock_req.call_args.kwargs["params"]
    assert params["count"] == 200


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_candles_default_timeframe_is_day(adapter: UpbitAdapter):
    """timeframe 기본값은 Timeframe.DAY다."""
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_RAW) as mock_req:
        await adapter.get_candles("KRW-BTC")

    endpoint = mock_req.call_args.args[1]
    assert endpoint == f"/candles/{Timeframe.DAY.value}"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_candles_includes_to_when_provided(adapter: UpbitAdapter):
    """to 인자가 전달되면 params에 포함된다."""
    to_str = "2025-01-03T00:00:00Z"
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_RAW) as mock_req:
        await adapter.get_candles("KRW-BTC", to=to_str)

    params = mock_req.call_args.kwargs["params"]
    assert params["to"] == to_str


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_candles_omits_to_when_not_provided(adapter: UpbitAdapter):
    """to 인자가 없으면 params에 포함되지 않는다."""
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_RAW) as mock_req:
        await adapter.get_candles("KRW-BTC")

    params = mock_req.call_args.kwargs["params"]
    assert "to" not in params


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_candles_stream_type_is_snapshot(adapter: UpbitAdapter):
    """모든 Candle의 stream_type이 SNAPSHOT이다."""
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_RAW):
        result = await adapter.get_candles("KRW-BTC")

    assert all(c.stream_type == StreamType.SNAPSHOT for c in result)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_candles_candle_fields_mapped_correctly(adapter: UpbitAdapter):
    """Candle 필드가 API 응답과 올바르게 매핑된다."""
    raw = _SAMPLE_RAW[1]  # 과거 항목 (역전 후 index 0)
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_RAW):
        result = await adapter.get_candles("KRW-BTC")

    candle = result[0]
    assert candle.code == raw["market"]
    assert candle.opening_price == raw["opening_price"]
    assert candle.high_price == raw["high_price"]
    assert candle.low_price == raw["low_price"]
    assert candle.trade_price == raw["trade_price"]
    assert candle.timestamp == raw["timestamp"]


# ---------------------------------------------------------------------------
# ORDER OPERATIONS
# ---------------------------------------------------------------------------

_SAMPLE_ORDER_RAW = {
    "uuid": "cdd11100-1111-1111-1111-000000000000",
    "side": "bid",
    "ord_type": "limit",
    "price": "100.0",
    "state": "wait",
    "market": "KRW-BTC",
    "created_at": "2023-01-01T00:00:00+09:00",
    "volume": "0.01",
    "remaining_volume": "0.01",
    "executed_volume": "0.0",
    "trades_count": 0,
    "reserved_fee": "0.0",
    "remaining_fee": "0.0",
    "paid_fee": "0.0",
    "locked": "1.0",
    "identifier": "my-id",
}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_order_limit_success(adapter: UpbitAdapter):
    """지정가 주문 성공 케이스"""
    from decimal import Decimal

    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_ORDER_RAW) as mock_req:
        result = await adapter.create_order(
            market="KRW-BTC", side=OrderSide.BID, ord_type=OrderType.LIMIT, volume=Decimal("0.01"), price=Decimal("100")
        )

    assert isinstance(result, Order)
    assert str(result.uuid) == _SAMPLE_ORDER_RAW["uuid"]
    params = mock_req.call_args.kwargs["params"]
    assert params["ord_type"] == "limit"
    assert params["volume"] == "0.01"
    assert params["price"] == "100"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_order_limit_missing_params_raises(adapter: UpbitAdapter):
    """지정가 주문 시 필수 파라미터 누락 시 ValueError"""
    from decimal import Decimal

    with pytest.raises(ValueError, match=r"지정가 주문\(limit\)은 `volume`과 `price`가 모두 필수"):
        await adapter.create_order(
            market="KRW-BTC", side=OrderSide.BID, ord_type=OrderType.LIMIT, volume=Decimal("0.01")
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_order_market_sell_success(adapter: UpbitAdapter):
    """시장가 매도 주문 성공 케이스"""
    from decimal import Decimal

    raw = _SAMPLE_ORDER_RAW.copy()
    raw["ord_type"] = "market"
    raw["side"] = "ask"

    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=raw) as mock_req:
        await adapter.create_order(
            market="KRW-BTC", side=OrderSide.ASK, ord_type=OrderType.MARKET, volume=Decimal("0.1")
        )

    params = mock_req.call_args.kwargs["params"]
    assert params["ord_type"] == "market"
    assert "price" not in params


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_order_market_sell_wrong_side_raises(adapter: UpbitAdapter):
    """시장가 매도 주문에 매수 side를 넣으면 ValueError"""
    from decimal import Decimal

    with pytest.raises(ValueError, match=r"시장가 매도 주문\(market\)은 `side=ask`여야 합니다"):
        await adapter.create_order(
            market="KRW-BTC", side=OrderSide.BID, ord_type=OrderType.MARKET, volume=Decimal("0.1")
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_order_price_buy_success(adapter: UpbitAdapter):
    """시장가 매수(총액) 주문 성공 케이스"""
    from decimal import Decimal

    raw = _SAMPLE_ORDER_RAW.copy()
    raw["ord_type"] = "price"

    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=raw) as mock_req:
        await adapter.create_order(
            market="KRW-BTC", side=OrderSide.BID, ord_type=OrderType.PRICE, price=Decimal("10000")
        )

    params = mock_req.call_args.kwargs["params"]
    assert params["ord_type"] == "price"
    assert params["price"] == "10000"
    assert "volume" not in params


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_order_best_success(adapter: UpbitAdapter):
    """최유리 지정가 주문 성공 케이스"""
    from decimal import Decimal

    raw = _SAMPLE_ORDER_RAW.copy()
    raw["ord_type"] = "best"

    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=raw) as mock_req:
        await adapter.create_order(
            market="KRW-BTC",
            side=OrderSide.BID,
            ord_type=OrderType.BEST,
            price=Decimal("10000"),
            time_in_force=TimeInForce.IOC,
        )

    params = mock_req.call_args.kwargs["params"]
    assert params["ord_type"] == "best"
    assert params["time_in_force"] == "ioc"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_order_best_missing_tif_raises(adapter: UpbitAdapter):
    """최유리 지정가 주문 시 ioc/fok가 아니면 ValueError"""
    from decimal import Decimal

    with pytest.raises(ValueError, match=r"최유리 지정가 주문\(best\)은 `time_in_force`를 `ioc` 또는 `fok`로 설정"):
        await adapter.create_order(
            market="KRW-BTC", side=OrderSide.BID, ord_type=OrderType.BEST, price=Decimal("10000")
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_order_post_only_with_smp_raises(adapter: UpbitAdapter):
    """post_only와 smp_type 혼용 시 ValueError"""
    from decimal import Decimal

    with pytest.raises(ValueError, match=r"`post_only` 옵션은 `smp_type` 옵션과 함께 사용할 수 없습니다"):
        await adapter.create_order(
            market="KRW-BTC",
            side=OrderSide.BID,
            ord_type=OrderType.LIMIT,
            volume=Decimal("0.1"),
            price=Decimal("100"),
            time_in_force=TimeInForce.POST_ONLY,
            smp_type=SmpType.CANCEL_MAKER,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_limit_order_wrapper(adapter: UpbitAdapter):
    """limit_order 래퍼 메서드 호출 확인"""
    from decimal import Decimal

    with patch.object(adapter, "create_order", new_callable=AsyncMock) as mock_create:
        await adapter.limit_order("KRW-BTC", OrderSide.BID, Decimal("0.1"), Decimal("100"))
        mock_create.assert_called_once_with(
            "KRW-BTC",
            OrderSide.BID,
            ord_type=OrderType.LIMIT,
            volume=Decimal("0.1"),
            price=Decimal("100"),
            time_in_force=None,
            smp_type=None,
            identifier=None,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_market_order_wrapper(adapter: UpbitAdapter):
    """market_order 래퍼 메서드 호출 확인"""
    from decimal import Decimal

    with patch.object(adapter, "create_order", new_callable=AsyncMock) as mock_create:
        await adapter.market_order("KRW-BTC", Decimal("0.1"))
        mock_create.assert_called_once_with(
            "KRW-BTC",
            side=OrderSide.ASK,
            ord_type=OrderType.MARKET,
            volume=Decimal("0.1"),
            smp_type=None,
            identifier=None,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_price_order_wrapper(adapter: UpbitAdapter):
    """price_order 래퍼 메서드 호출 확인"""
    from decimal import Decimal

    with patch.object(adapter, "create_order", new_callable=AsyncMock) as mock_create:
        await adapter.price_order("KRW-BTC", Decimal("10000"))
        mock_create.assert_called_once_with(
            "KRW-BTC",
            side=OrderSide.BID,
            ord_type=OrderType.PRICE,
            price=Decimal("10000"),
            smp_type=None,
            identifier=None,
        )


# ---------------------------------------------------------------------------
# ACCOUNT OPERATIONS
# ---------------------------------------------------------------------------

_SAMPLE_ACCOUNTS_RAW = [
    {
        "currency": "KRW",
        "balance": "1000000.0",
        "locked": "0.0",
        "avg_buy_price": "0",
        "avg_buy_price_modified": False,
        "unit_currency": "KRW",
    },
    {
        "currency": "BTC",
        "balance": "0.1",
        "locked": "0.0",
        "avg_buy_price": "50000000.0",
        "avg_buy_price_modified": False,
        "unit_currency": "KRW",
    },
]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_assets_success(adapter: UpbitAdapter):
    """전체 자산 조회 성공 케이스"""
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_ACCOUNTS_RAW) as mock_req:
        result = await adapter.get_assets()

    assert len(result) == 2
    assert all(isinstance(a, Asset) for a in result)
    assert result[0].currency == "KRW"
    assert result[1].currency == "BTC"
    mock_req.assert_awaited_once_with("GET", "/accounts", signed=True)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_assets_failure_returns_empty_list(adapter: UpbitAdapter):
    """API 요청 실패 시 빈 리스트 반환"""
    with patch.object(adapter, "_request", new_callable=AsyncMock, side_effect=Exception("API Error")):
        result = await adapter.get_assets()

    assert result == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_asset_exists(adapter: UpbitAdapter):
    """존재하는 통화의 자산 정보 조회"""
    with patch.object(adapter, "get_assets", new_callable=AsyncMock) as mock_get_assets:
        mock_get_assets.return_value = [Asset.from_dict(r) for r in _SAMPLE_ACCOUNTS_RAW]
        result = await adapter.get_asset("BTC")

    assert result is not None
    assert result.currency == "BTC"
    assert result.balance == Decimal("0.1")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_asset_not_exists(adapter: UpbitAdapter):
    """존재하지 않는 통화 조회 시 None 반환"""
    with patch.object(adapter, "get_assets", new_callable=AsyncMock) as mock_get_assets:
        mock_get_assets.return_value = [Asset.from_dict(r) for r in _SAMPLE_ACCOUNTS_RAW]
        result = await adapter.get_asset("ETH")

    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_assets_empty_response(adapter: UpbitAdapter):
    """빈 응답이면 빈 리스트를 반환한다."""
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=[]):
        result = await adapter.get_assets()

    assert result == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_assets_asset_fields_mapped(adapter: UpbitAdapter):
    """각 Asset의 balance, avg_buy_price가 Decimal로 올바르게 매핑된다."""
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_ACCOUNTS_RAW):
        result = await adapter.get_assets()

    btc = next(a for a in result if a.currency == "BTC")
    assert btc.balance == Decimal("0.1")
    assert btc.avg_buy_price == Decimal("50000000.0")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_asset_calls_get_assets(adapter: UpbitAdapter):
    """get_asset()은 내부적으로 get_assets()를 호출한다."""
    with patch.object(adapter, "get_assets", new_callable=AsyncMock, return_value=[]) as mock_get:
        await adapter.get_asset("KRW")

    mock_get.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_asset_returns_first_match(adapter: UpbitAdapter):
    """동일 currency가 여럿일 때 첫 번째 항목을 반환한다."""
    duplicate_raw = [
        *_SAMPLE_ACCOUNTS_RAW,
        {
            "currency": "KRW",
            "balance": "999.0",
            "locked": "0.0",
            "avg_buy_price": "0",
            "avg_buy_price_modified": False,
            "unit_currency": "KRW",
        },
    ]
    with patch.object(
        adapter, "get_assets", new_callable=AsyncMock, return_value=[Asset.from_dict(r) for r in duplicate_raw]
    ):
        result = await adapter.get_asset("KRW")

    assert result is not None
    assert result.balance == Decimal("1000000.0")


# _timeframe_to_candle_type


@pytest.mark.unit
@pytest.mark.parametrize(
    "timeframe, expected",
    [
        (Timeframe.SECOND, CandleType.SECOND),
        (Timeframe.MINUTE_1, CandleType.MINUTE_1),
        (Timeframe.MINUTE_3, CandleType.MINUTE_3),
        (Timeframe.MINUTE_5, CandleType.MINUTE_5),
        (Timeframe.HALF_HOUR, CandleType.HALF_HOUR),
        (Timeframe.HOUR, CandleType.HOUR),
        (Timeframe.HOUR_4, CandleType.HOUR_4),
    ],
)
def test_timeframe_to_candle_type_mapping(timeframe: Timeframe, expected: CandleType):
    """각 Timeframe이 대응하는 CandleType으로 변환된다."""
    assert UpbitAdapter._timeframe_to_candle_type(timeframe) == expected


# ---------------------------------------------------------------------------
# get_tickers / get_ticker
# ---------------------------------------------------------------------------

_SAMPLE_TICKER_RAW = [
    {
        "market": "KRW-BTC",
        "trade_date": "20250103",
        "trade_time": "120000",
        "trade_timestamp": 1735905600000,
        "opening_price": 143000000.0,
        "high_price": 145000000.0,
        "low_price": 142000000.0,
        "trade_price": 144000000.0,
        "prev_closing_price": 142500000.0,
        "change": "RISE",
        "change_price": 1500000.0,
        "change_rate": 0.0105,
        "signed_change_price": 1500000.0,
        "signed_change_rate": 0.0105,
        "trade_volume": 0.001,
        "acc_trade_volume": 35.12,
        "acc_trade_volume_24h": 70.0,
        "acc_trade_price": 5000000000.0,
        "acc_trade_price_24h": 10000000000.0,
        "highest_52_week_price": 200000000.0,
        "highest_52_week_date": "2024-01-01",
        "lowest_52_week_price": 50000000.0,
        "lowest_52_week_date": "2024-06-01",
        "market_state": "ACTIVE",
        "timestamp": 1735905600000,
        "acc_bid_volume": 0.0,
        "acc_ask_volume": 0.0,
    },
    {
        "market": "KRW-ETH",
        "trade_date": "20250103",
        "trade_time": "120000",
        "trade_timestamp": 1735905600000,
        "opening_price": 5000000.0,
        "high_price": 5200000.0,
        "low_price": 4900000.0,
        "trade_price": 5100000.0,
        "prev_closing_price": 4950000.0,
        "change": "RISE",
        "change_price": 150000.0,
        "change_rate": 0.0303,
        "signed_change_price": 150000.0,
        "signed_change_rate": 0.0303,
        "trade_volume": 0.1,
        "acc_trade_volume": 100.0,
        "acc_trade_volume_24h": 200.0,
        "acc_trade_price": 500000000.0,
        "acc_trade_price_24h": 1000000000.0,
        "highest_52_week_price": 7000000.0,
        "highest_52_week_date": "2024-03-01",
        "lowest_52_week_price": 2000000.0,
        "lowest_52_week_date": "2024-09-01",
        "market_state": "ACTIVE",
        "timestamp": 1735905600000,
        "acc_bid_volume": 0.0,
        "acc_ask_volume": 0.0,
    },
]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_tickers_returns_list_of_tickers(adapter: UpbitAdapter):
    """반환값이 Ticker 리스트다."""
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_TICKER_RAW):
        result = await adapter.get_tickers(["KRW-BTC", "KRW-ETH"])

    assert isinstance(result, list)
    assert all(isinstance(t, Ticker) for t in result)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_tickers_count_matches_response(adapter: UpbitAdapter):
    """응답 항목 수와 반환 리스트 길이가 일치한다."""
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_TICKER_RAW):
        result = await adapter.get_tickers(["KRW-BTC", "KRW-ETH"])

    assert len(result) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_tickers_calls_correct_endpoint(adapter: UpbitAdapter):
    """_request()가 /ticker 엔드포인트로 호출된다."""
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_TICKER_RAW) as mock_req:
        await adapter.get_tickers(["KRW-BTC"])

    assert mock_req.call_args.args[1] == "/ticker"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_tickers_passes_markets_as_comma_joined(adapter: UpbitAdapter):
    """markets 목록이 쉼표로 연결되어 params에 전달된다."""
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_TICKER_RAW) as mock_req:
        await adapter.get_tickers(["KRW-BTC", "KRW-ETH"])

    params = mock_req.call_args.kwargs["params"]
    assert params["markets"] == "KRW-BTC,KRW-ETH"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_tickers_market_mapped_to_code(adapter: UpbitAdapter):
    """REST 응답의 market 필드가 Ticker.code 로 매핑된다."""
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_TICKER_RAW):
        result = await adapter.get_tickers(["KRW-BTC", "KRW-ETH"])

    assert result[0].code == "KRW-BTC"
    assert result[1].code == "KRW-ETH"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_tickers_type_is_ticker(adapter: UpbitAdapter):
    """Ticker.type 은 'ticker' 고정값이다."""
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_TICKER_RAW):
        result = await adapter.get_tickers(["KRW-BTC"])

    assert result[0].type == "ticker"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_tickers_stream_type_is_snapshot(adapter: UpbitAdapter):
    """Ticker.stream_type 은 SNAPSHOT이다."""
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_TICKER_RAW):
        result = await adapter.get_tickers(["KRW-BTC"])

    assert result[0].stream_type == StreamType.SNAPSHOT


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_tickers_ask_bid_default_is_bid(adapter: UpbitAdapter):
    """REST API 미제공 ask_bid 필드의 기본값은 AskBid.BID다."""
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_TICKER_RAW):
        result = await adapter.get_tickers(["KRW-BTC"])

    assert result[0].ask_bid == AskBid.BID


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_tickers_price_fields_are_decimal(adapter: UpbitAdapter):
    """trade_price 등 가격 필드가 Decimal로 변환된다."""
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_TICKER_RAW):
        result = await adapter.get_tickers(["KRW-BTC"])

    t = result[0]
    assert isinstance(t.trade_price, Decimal)
    assert isinstance(t.opening_price, Decimal)
    assert isinstance(t.high_price, Decimal)
    assert isinstance(t.low_price, Decimal)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_tickers_change_is_enum(adapter: UpbitAdapter):
    """change 필드가 ChangeDirection Enum으로 변환된다."""
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_TICKER_RAW):
        result = await adapter.get_tickers(["KRW-BTC"])

    assert result[0].change == ChangeDirection.RISE


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_tickers_market_state_is_enum(adapter: UpbitAdapter):
    """market_state 필드가 MarketState Enum으로 변환된다."""
    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=_SAMPLE_TICKER_RAW):
        result = await adapter.get_tickers(["KRW-BTC"])

    assert result[0].market_state == MarketState.ACTIVE


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_tickers_52week_fields_none_when_missing(adapter: UpbitAdapter):
    """highest/lowest_52_week 필드가 응답에 없으면 None이다."""
    raw = [{**_SAMPLE_TICKER_RAW[0]}]
    del raw[0]["highest_52_week_price"]
    del raw[0]["highest_52_week_date"]
    del raw[0]["lowest_52_week_price"]
    del raw[0]["lowest_52_week_date"]

    with patch.object(adapter, "_request", new_callable=AsyncMock, return_value=raw):
        result = await adapter.get_tickers(["KRW-BTC"])

    assert result[0].highest_52_week_price is None
    assert result[0].highest_52_week_date is None
    assert result[0].lowest_52_week_price is None
    assert result[0].lowest_52_week_date is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_ticker_returns_single_ticker(adapter: UpbitAdapter):
    """단건 조회 시 Ticker 인스턴스를 반환한다."""
    with patch.object(
        adapter,
        "get_tickers",
        new_callable=AsyncMock,
        return_value=[
            Ticker(
                **{
                    **{f: getattr(Ticker, f, None) for f in []},
                    "type": "ticker",
                    "code": "KRW-BTC",
                    "opening_price": Decimal("143000000"),
                    "high_price": Decimal("145000000"),
                    "low_price": Decimal("142000000"),
                    "trade_price": Decimal("144000000"),
                    "prev_closing_price": Decimal("142500000"),
                    "change": ChangeDirection.RISE,
                    "change_price": Decimal("1500000"),
                    "signed_change_price": Decimal("1500000"),
                    "change_rate": Decimal("0.0105"),
                    "signed_change_rate": Decimal("0.0105"),
                    "trade_volume": Decimal("0.001"),
                    "acc_trade_volume": Decimal("35.12"),
                    "acc_trade_volume_24h": Decimal("70.0"),
                    "acc_trade_price": Decimal("5000000000"),
                    "acc_trade_price_24h": Decimal("10000000000"),
                    "trade_date": "20250103",
                    "trade_time": "120000",
                    "trade_timestamp": 1735905600000,
                    "ask_bid": AskBid.BID,
                    "acc_ask_volume": Decimal("0"),
                    "acc_bid_volume": Decimal("0"),
                    "highest_52_week_price": Decimal("200000000"),
                    "highest_52_week_date": "2024-01-01",
                    "lowest_52_week_price": Decimal("50000000"),
                    "lowest_52_week_date": "2024-06-01",
                    "market_state": MarketState.ACTIVE,
                    "timestamp": 1735905600000,
                    "stream_type": StreamType.SNAPSHOT,
                }
            )
        ],
    ):
        result = await adapter.get_ticker("KRW-BTC")

    assert isinstance(result, Ticker)
    assert result.code == "KRW-BTC"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_ticker_passes_market_as_single_element_list(adapter: UpbitAdapter):
    """get_ticker는 get_tickers를 단일 원소 리스트로 호출한다."""
    with patch.object(adapter, "get_tickers", new_callable=AsyncMock, return_value=[]) as mock_tickers:
        await adapter.get_ticker("KRW-BTC")

    mock_tickers.assert_awaited_once_with(["KRW-BTC"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_ticker_returns_none_when_empty(adapter: UpbitAdapter):
    """get_tickers가 빈 리스트를 반환하면 None을 반환한다."""
    with patch.object(adapter, "get_tickers", new_callable=AsyncMock, return_value=[]):
        result = await adapter.get_ticker("KRW-BTC")

    assert result is None
