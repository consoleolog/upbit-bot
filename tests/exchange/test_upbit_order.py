"""업비트 주문 생성·주문 생성 테스트·주문 조회. 정상 응답은 업비트 개발자 문서의 응답 예시를 바탕으로 한다."""

import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import unquote, urlencode

import httpx
import pytest

from src.exchange.order import Order, OrderSide, OrderState, OrderType, Trade
from src.exchange.upbit_client import (
    UpbitClient,
    UpbitNotFoundError,
    UpbitRequestError,
    UpbitResponseError,
    UpbitResponseResultUnknownError,
    UpbitServerResultUnknownError,
    UpbitUnknownResultError,
)
from tests.exchange.test_upbit_account import CREDENTIALS, CountingLimiter, Upbit, _payload

KST = timezone(timedelta(hours=9))

# 업비트 문서 "개별 주문 조회" 응답 예시(시장가)에 identifier를 더했다
DONE_SELL = {
    "market": "KRW-USDT",
    "uuid": "3b67e543-8ad3-48d0-8451-0dad315cae73",
    "side": "ask",
    "ord_type": "market",
    "state": "done",
    "created_at": "2025-08-09T16:44:00+09:00",
    "volume": "5.377594",
    "remaining_volume": "0",
    "executed_volume": "5.377594",
    "reserved_fee": "0",
    "remaining_fee": "0",
    "paid_fee": "3.697095875",
    "locked": "0",
    "prevented_volume": "0",
    "prevented_locked": "0",
    "trades_count": 1,
    "identifier": "sell-1",
    "trades": [
        {
            "market": "KRW-USDT",
            "uuid": "795dff29-bba6-49b2-baab-63473ab7931c",
            "price": "1375",
            "volume": "5.377594",
            "funds": "7394.19175",
            "trend": "down",
            "created_at": "2025-08-09T16:44:00.597751+09:00",
            "side": "ask",
        }
    ],
}

# 업비트 문서 "주문 생성" 응답 예시의 형식으로 만든 시장가 매수 접수 응답 (volume 없음, trades 없음)
NEW_BUY = {
    "market": "KRW-BTC",
    "uuid": "cdd92199-2897-4e14-9448-f923320408ad",
    "side": "bid",
    "ord_type": "price",
    "price": "5000",
    "state": "wait",
    "created_at": "2025-07-04T15:00:00+09:00",
    "remaining_volume": None,
    "reserved_fee": "2.5",
    "remaining_fee": "2.5",
    "paid_fee": "0",
    "locked": "5002.5",
    "executed_volume": "0",
    "prevented_volume": "0",
    "prevented_locked": "0",
    "trades_count": 0,
    "identifier": "buy-1",
}

NEW_SELL = {**NEW_BUY, "side": "ask", "ord_type": "market", "price": None, "volume": "0.001", "identifier": "sell-1"}


def _client(upbit: Upbit, **limiters: CountingLimiter) -> UpbitClient:
    return UpbitClient(transport=httpx.MockTransport(upbit), credentials=CREDENTIALS, **limiters)  # type: ignore[arg-type]


async def _call(upbit: Upbit, method: str, *args: object) -> Any:
    async with _client(upbit) as client:
        return await getattr(client, method)(*args)


def _limiters() -> dict[str, CountingLimiter]:
    return {f"{name}_limiter": CountingLimiter() for name in ("candle", "default", "order", "order_test")}


def _counts(limiters: dict[str, CountingLimiter]) -> dict[str, int]:
    return {name.removesuffix("_limiter"): limiter.count for name, limiter in limiters.items()}


@pytest.mark.parametrize(
    ("method", "path", "group"),
    [("place_market_buy", "/v1/orders", "order"), ("test_market_buy", "/v1/orders/test", "order_test")],
)
async def test_market_buy_sends_total_price(method: str, path: str, group: str) -> None:
    upbit = Upbit(201, NEW_BUY)
    limiters = _limiters()
    async with _client(upbit, **limiters) as client:
        order = await getattr(client, method)("KRW-BTC", Decimal("5000"), "buy-1")

    request = upbit.requests[0]
    assert (request.method, request.url.path) == ("POST", path)
    body = json.loads(request.read())
    # 시장가 매수: 원화 총액을 price로 보내고 volume은 보내지 않는다
    assert body == {"market": "KRW-BTC", "side": "bid", "ord_type": "price", "price": "5000", "identifier": "buy-1"}
    assert _payload(request)["query_hash"] == hashlib.sha512(unquote(urlencode(body)).encode()).hexdigest()
    # 업비트는 주문과 주문 생성 테스트의 요청 수를 따로 센다
    assert _counts(limiters) == {"candle": 0, "default": 0, "order": 0, "order_test": 0, group: 1}
    assert order == Order(
        uuid="cdd92199-2897-4e14-9448-f923320408ad",
        identifier="buy-1",
        market="KRW-BTC",
        side=OrderSide.BID,
        ord_type=OrderType.PRICE,
        state=OrderState.WAIT,
        created_at=datetime(2025, 7, 4, 15, tzinfo=KST),
        price=Decimal("5000"),
        volume=None,
        executed_volume=Decimal("0"),
        paid_fee=Decimal("0"),
        trades_count=0,
        trades=(),
    )


@pytest.mark.parametrize(
    ("method", "path", "group"),
    [("place_market_sell", "/v1/orders", "order"), ("test_market_sell", "/v1/orders/test", "order_test")],
)
async def test_market_sell_sends_volume(method: str, path: str, group: str) -> None:
    upbit = Upbit(201, NEW_SELL)
    limiters = _limiters()
    async with _client(upbit, **limiters) as client:
        order = await getattr(client, method)("KRW-BTC", Decimal("1E-3"), "sell-1")

    request = upbit.requests[0]
    assert (request.method, request.url.path) == ("POST", path)
    body = json.loads(request.read())
    # 시장가 매도: 수량을 volume으로 보내고 price는 보내지 않는다. 지수 표기 없이 보낸다
    assert body == {"market": "KRW-BTC", "side": "ask", "ord_type": "market", "volume": "0.001", "identifier": "sell-1"}
    assert _payload(request)["query_hash"] == hashlib.sha512(unquote(urlencode(body)).encode()).hexdigest()
    assert _counts(limiters) == {"candle": 0, "default": 0, "order": 0, "order_test": 0, group: 1}
    assert (order.side, order.ord_type, order.price, order.volume) == (
        OrderSide.ASK,
        OrderType.MARKET,
        None,
        Decimal("0.001"),
    )


@pytest.mark.parametrize("method", ["place_market_buy", "place_market_sell", "test_market_buy", "test_market_sell"])
@pytest.mark.parametrize(
    ("amount", "identifier", "message"),
    [
        (Decimal("5000"), "", "identifier"),
        (Decimal("5000"), "x" * 65, "identifier"),
        (Decimal("0"), "id-1", "positive finite"),
        (Decimal("-1"), "id-1", "positive finite"),
        (Decimal("NaN"), "id-1", "positive finite"),
    ],
)
async def test_invalid_order_is_not_sent(method: str, amount: Decimal, identifier: str, message: str) -> None:
    upbit = Upbit(201, NEW_BUY)

    with pytest.raises(ValueError, match=message):
        await _call(upbit, method, "KRW-BTC", amount, identifier)

    assert upbit.requests == []


async def test_identifier_of_max_length_is_sent() -> None:
    identifier = "x" * 64
    upbit = Upbit(201, {**NEW_BUY, "identifier": identifier})

    order = await _call(upbit, "place_market_buy", "KRW-BTC", Decimal("5000"), identifier)

    assert order.identifier == identifier


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {**NEW_BUY, "uuid": ""},
        {**NEW_BUY, "state": "unknown"},
        {**NEW_BUY, "created_at": "2025-07-04T15:00:00"},
        {**NEW_BUY, "executed_volume": 0},
        {**NEW_BUY, "paid_fee": None},
        {**NEW_BUY, "trades_count": True},
        # 요청과 다른 주문이 돌아옴
        {**NEW_BUY, "identifier": "buy-2"},
        {**NEW_BUY, "identifier": None},
        {**NEW_BUY, "market": "KRW-ETH"},
        {**NEW_BUY, "side": "ask"},
        {**NEW_BUY, "ord_type": "limit"},
    ],
)
async def test_unreadable_order_response_is_result_unknown(body: object) -> None:
    upbit = Upbit(201, body)

    # 성공 응답을 받았으면 주문은 접수됐을 수 있다. 실패로 보고 다시 보내면 중복 주문이 된다
    with pytest.raises(UpbitResponseResultUnknownError):
        await _call(upbit, "place_market_buy", "KRW-BTC", Decimal("5000"), "buy-1")

    assert len(upbit.requests) == 1


async def test_server_error_on_order_is_result_unknown_and_not_resent() -> None:
    upbit = Upbit(500, {"error": {"name": "server_error", "message": "internal"}})

    with pytest.raises(UpbitServerResultUnknownError) as error:
        await _call(upbit, "place_market_sell", "KRW-BTC", Decimal("0.001"), "sell-1")

    assert isinstance(error.value, UpbitUnknownResultError)
    assert len(upbit.requests) == 1


async def test_rejected_order_raises_request_error() -> None:
    # 업비트 문서의 400 예시: 잔고 부족
    upbit = Upbit(400, {"error": {"name": "insufficient_funds_bid", "message": "주문 가능한 금액(KRW)이 부족합니다."}})

    with pytest.raises(UpbitRequestError) as error:
        await _call(upbit, "place_market_buy", "KRW-BTC", Decimal("5000"), "buy-1")

    assert error.value.name == "insufficient_funds_bid"
    assert not isinstance(error.value, UpbitUnknownResultError)


async def test_get_order_by_identifier() -> None:
    upbit = Upbit(200, DONE_SELL)
    limiters = _limiters()
    async with _client(upbit, **limiters) as client:
        order = await client.get_order("sell-1")

    request = upbit.requests[0]
    assert (request.method, request.url.path, request.url.query) == ("GET", "/v1/order", b"identifier=sell-1")
    assert _payload(request)["query_hash"] == hashlib.sha512(b"identifier=sell-1").hexdigest()
    assert _counts(limiters) == {"candle": 0, "default": 1, "order": 0, "order_test": 0}
    assert order == Order(
        uuid="3b67e543-8ad3-48d0-8451-0dad315cae73",
        identifier="sell-1",
        market="KRW-USDT",
        side=OrderSide.ASK,
        ord_type=OrderType.MARKET,
        state=OrderState.DONE,
        created_at=datetime(2025, 8, 9, 16, 44, tzinfo=KST),
        price=None,
        volume=Decimal("5.377594"),
        executed_volume=Decimal("5.377594"),
        paid_fee=Decimal("3.697095875"),
        trades_count=1,
        trades=(
            Trade(
                price=Decimal("1375"),
                volume=Decimal("5.377594"),
                funds=Decimal("7394.19175"),
                created_at=datetime(2025, 8, 9, 7, 44, 0, 597751, tzinfo=UTC),
            ),
        ),
    )


async def test_cancelled_market_order_keeps_executed_volume() -> None:
    # 시장가 주문이 일부만 체결되고 나머지가 취소되면 state는 cancel이지만 체결된 양이 있다
    body = {**DONE_SELL, "state": "cancel", "executed_volume": "2", "remaining_volume": "3.377594"}

    order = await _call(Upbit(200, body), "get_order", "sell-1")

    assert order.state is OrderState.CANCEL
    assert order.executed_volume == Decimal("2")


async def test_get_order_returns_none_when_order_does_not_exist() -> None:
    # 업비트 문서의 404 예시
    upbit = Upbit(404, {"error": {"name": "order_not_found", "message": "주문을 찾지 못했습니다."}})

    assert await _call(upbit, "get_order", "sell-1") is None


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        # 주문이 없다는 확인이 아니면 없다고 단정하지 않는다
        (404, {"error": {"name": "not_found", "message": "?"}}, UpbitNotFoundError),
        (404, "not json", UpbitNotFoundError),
        (400, {"error": {"name": "invalid_parameter", "message": "잘못된 파라미터"}}, UpbitRequestError),
        (200, {**DONE_SELL, "identifier": "sell-2"}, UpbitResponseError),
        (200, {**DONE_SELL, "trades": [{"price": "1375"}]}, UpbitResponseError),
        (200, {**DONE_SELL, "trades": None}, UpbitResponseError),
    ],
)
async def test_get_order_failures_raise(status: int, body: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        await _call(Upbit(status, body), "get_order", "sell-1")


async def test_get_order_rejects_invalid_identifier() -> None:
    upbit = Upbit(200, DONE_SELL)

    with pytest.raises(ValueError, match="identifier"):
        await _call(upbit, "get_order", "")

    assert upbit.requests == []
