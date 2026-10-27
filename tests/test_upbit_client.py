import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest

from src.candle import KST, Candle
from src.config import Timeframe
from src.rate_limiter import RateLimiter
from src.upbit_client import (
    Method,
    UpbitAuthError,
    UpbitBlockedError,
    UpbitClient,
    UpbitConnectionError,
    UpbitError,
    UpbitRateLimitError,
    UpbitRequestError,
    UpbitResponseError,
    UpbitServerError,
    UpbitTimeoutError,
    UpbitUnknownResultError,
    _parse_candle,
)

Handler = Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]


async def _request(
    handler: Handler, method: Method = "GET", params: dict[str, Any] | None = None, timeout: float = 5.0
) -> Any:
    """요청을 handler로 처리하는 가짜 연결로 요청한다. 네트워크를 쓰지 않는다."""
    client = UpbitClient(transport=httpx.MockTransport(handler), timeout=timeout)
    await client.open()
    try:
        return await client._request(method, "/v1/test", params)
    finally:
        await client.close()


async def test_get_sends_query_params_and_returns_json() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[{"market": "KRW-BTC"}])

    result = await _request(handler, params={"market": "KRW-BTC", "count": 200})

    assert result == [{"market": "KRW-BTC"}]
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/v1/test"
    assert dict(seen[0].url.params) == {"market": "KRW-BTC", "count": "200"}


async def test_post_sends_json_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"uuid": "abc"})

    result = await _request(handler, method="POST", params={"market": "KRW-BTC"})

    assert result == {"uuid": "abc"}
    assert seen[0].method == "POST"
    assert seen[0].read() == b'{"market":"KRW-BTC"}'


@pytest.mark.parametrize(
    ("status", "error_class"),
    [
        (400, UpbitRequestError),
        (401, UpbitAuthError),
        (418, UpbitBlockedError),
        (429, UpbitRateLimitError),
        (500, UpbitServerError),
        (503, UpbitServerError),
        (404, UpbitError),
    ],
)
async def test_error_status_raises_matching_error(status: int, error_class: type[UpbitError]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"name": "some_error", "message": "something wrong"}})

    with pytest.raises(error_class) as exc_info:
        await _request(handler)

    assert type(exc_info.value) is error_class
    assert exc_info.value.status == status
    assert exc_info.value.name == "some_error"
    assert "something wrong" in str(exc_info.value)


async def test_unknown_error_name_still_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"name": "never_seen_before", "message": "?"}})

    with pytest.raises(UpbitRequestError) as exc_info:
        await _request(handler)

    assert exc_info.value.name == "never_seen_before"


async def test_error_with_non_json_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="Too many API requests.")

    with pytest.raises(UpbitRateLimitError) as exc_info:
        await _request(handler)

    assert exc_info.value.name is None
    assert "Too many API requests." in str(exc_info.value)


async def test_error_with_unexpected_json_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json=["unexpected"])

    with pytest.raises(UpbitRequestError) as exc_info:
        await _request(handler)

    assert exc_info.value.name is None


async def test_success_with_invalid_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>maintenance</html>")

    with pytest.raises(UpbitResponseError):
        await _request(handler)


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("refused"),
        httpx.ConnectTimeout("connect timed out"),
        httpx.PoolTimeout("no free connection"),
        httpx.ProxyError("proxy refused"),
        httpx.UnsupportedProtocol("bad scheme"),
    ],
)
async def test_failure_before_sending_is_connection_error(error: httpx.TransportError) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    with pytest.raises(UpbitConnectionError):
        await _request(handler)


@pytest.mark.parametrize(
    "error",
    [httpx.ReadError("reset"), httpx.WriteError("broken pipe"), httpx.RemoteProtocolError("server disconnected")],
)
async def test_failure_after_sending_is_unknown_result(error: httpx.TransportError) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    with pytest.raises(UpbitUnknownResultError) as exc_info:
        await _request(handler, method="POST", params={"market": "KRW-BTC"})

    # 연결 실패로 보이면 호출하는 쪽이 주문을 다시 낼 수 있다
    assert not isinstance(exc_info.value, UpbitConnectionError)


@pytest.mark.parametrize("error", [httpx.ReadTimeout("read timed out"), httpx.WriteTimeout("write timed out")])
async def test_timeout_while_waiting_is_unknown_result(error: httpx.TimeoutException) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    with pytest.raises(UpbitTimeoutError) as exc_info:
        await _request(handler)

    assert isinstance(exc_info.value, UpbitUnknownResultError)


async def test_whole_request_time_limited() -> None:
    # httpx의 단계별 시간 제한에 걸리지 않고 전체가 오래 걸리는 응답
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(10)
        return httpx.Response(200, json={})

    with pytest.raises(UpbitTimeoutError):
        await asyncio.wait_for(_request(slow, timeout=0.05), 1.0)


async def test_request_before_open() -> None:
    client = UpbitClient()

    with pytest.raises(RuntimeError, match="not open"):
        await client._request("GET", "/v1/test")


async def test_request_after_close() -> None:
    client = UpbitClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    await client.open()
    await client.close()

    with pytest.raises(RuntimeError, match="not open"):
        await client._request("GET", "/v1/test")


async def test_open_twice_rejected() -> None:
    client = UpbitClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    await client.open()

    with pytest.raises(RuntimeError, match="already open"):
        await client.open()
    await client.close()


async def test_close_twice_allowed() -> None:
    client = UpbitClient()
    await client.open()

    await client.close()
    await client.close()


@pytest.mark.parametrize("timeout", [0, -1])
def test_invalid_timeout_rejected(timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout"):
        UpbitClient(timeout=timeout)


async def test_success_with_invalid_utf8_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"market": "\xff\xfe"}', headers={"Content-Type": "application/json"})

    with pytest.raises(UpbitResponseError):
        await _request(handler)


async def test_error_with_invalid_utf8_body_keeps_status_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, content=b"\xff\xfe too many", headers={"Content-Type": "application/json"})

    with pytest.raises(UpbitRateLimitError) as exc_info:
        await _request(handler)

    assert exc_info.value.name is None


def _raw_candle(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "market": "KRW-BTC",
        "candle_date_time_utc": "2026-01-01T00:30:00",
        "candle_date_time_kst": "2026-01-01T09:30:00",
        "opening_price": 100.0,
        "high_price": 120.0,
        "low_price": 90.0,
        "trade_price": 110.0,
    }
    raw.update(overrides)
    return raw


def test_parse_candle() -> None:
    assert _parse_candle(_raw_candle()) == Candle(
        market="KRW-BTC",
        start=datetime(2026, 1, 1, 0, 30, tzinfo=UTC),
        open=Decimal("100.0"),
        high=Decimal("120.0"),
        low=Decimal("90.0"),
        close=Decimal("110.0"),
    )


def test_parse_candle_keeps_written_float_value() -> None:
    price = 0.0001234
    candle = _parse_candle(_raw_candle(opening_price=price, high_price=price, low_price=price, trade_price=price))

    # Decimal(0.0001234)처럼 바로 바꾸면 이진 부동소수점 오차가 그대로 들어간다
    assert candle.close == Decimal("0.0001234")


@pytest.mark.parametrize(
    "overrides",
    [
        {"market": None},
        {"market": 1},
        {"candle_date_time_utc": None},
        {"candle_date_time_utc": 20260101},
        {"candle_date_time_utc": "not a time"},
        {"candle_date_time_utc": "2026-01-01"},
        {"candle_date_time_utc": "2026-01-01T00:30:00+09:00"},
        {"candle_date_time_utc": "2026-01-01T00:30:00Z"},
        {"trade_price": None},
        {"trade_price": "110.0"},
        {"trade_price": True},
        {"trade_price": 0},
        {"trade_price": -1},
        {"trade_price": float("nan")},
        {"trade_price": float("inf")},
        {"low_price": 115.0},
        {"high_price": 105.0},
    ],
)
def test_parse_candle_rejects_invalid(overrides: dict[str, object]) -> None:
    with pytest.raises(UpbitResponseError, match="invalid upbit candle"):
        _parse_candle(_raw_candle(**overrides))


@pytest.mark.parametrize("raw", [None, [], "KRW-BTC", {"market": "KRW-BTC"}])
def test_parse_candle_rejects_wrong_shape(raw: object) -> None:
    with pytest.raises(UpbitResponseError, match="invalid upbit candle"):
        _parse_candle(raw)


TO = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)


async def _get_candles(
    response: object, timeframe: Timeframe = Timeframe.MINUTE_30, count: int = 200, to: datetime = TO
) -> tuple[list[Candle], httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=response)

    client = UpbitClient(transport=httpx.MockTransport(handler))
    await client.open()
    try:
        return await client.get_candles("KRW-BTC", timeframe, to, count), seen[0]
    finally:
        await client.close()


async def test_get_candles_returns_oldest_first() -> None:
    # 업비트는 최신 → 과거 순으로 준다
    response = [
        _raw_candle(candle_date_time_utc="2026-01-01T00:30:00"),
        _raw_candle(candle_date_time_utc="2026-01-01T00:00:00"),
    ]

    candles, request = await _get_candles(response)

    assert [candle.start for candle in candles] == [
        datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 30, tzinfo=UTC),
    ]
    assert request.url.path == "/v1/candles/minutes/30"
    assert dict(request.url.params) == {"market": "KRW-BTC", "count": "200", "to": "2026-01-01T01:00:00Z"}


@pytest.mark.parametrize(("timeframe", "unit"), [(Timeframe.MINUTE_1, "1"), (Timeframe.MINUTE_240, "240")])
async def test_get_candles_uses_timeframe_unit(timeframe: Timeframe, unit: str) -> None:
    # 00:00(UTC)은 1분봉과 240분봉 모두의 경계다
    _, request = await _get_candles([], timeframe=timeframe, to=datetime(2026, 1, 1, tzinfo=UTC))

    assert request.url.path == f"/v1/candles/minutes/{unit}"


async def test_get_candles_sends_to_in_utc() -> None:
    seen: list[httpx.Request] = []
    client = UpbitClient(
        transport=httpx.MockTransport(lambda request: seen.append(request) or httpx.Response(200, json=[]))
    )
    await client.open()
    await client.get_candles("KRW-BTC", Timeframe.MINUTE_30, TO.astimezone(KST))
    await client.close()

    assert seen[0].url.params["to"] == "2026-01-01T01:00:00Z"


@pytest.mark.parametrize(
    "response",
    [
        {"error": "not a list"},
        [_raw_candle(market="KRW-ETH")],
        # to 이후에 시작한 캔들(진행 중인 캔들 등)이 섞이면 안 된다
        [_raw_candle(candle_date_time_utc="2026-01-01T01:00:00")],
        [_raw_candle(candle_date_time_utc="2026-01-01T01:30:00")],
        [_raw_candle(trade_price=None)],
    ],
)
async def test_get_candles_rejects_unexpected_response(response: object) -> None:
    with pytest.raises(UpbitResponseError):
        await _get_candles(response)


async def test_get_candles_rejects_more_than_requested() -> None:
    response = [
        _raw_candle(candle_date_time_utc="2026-01-01T00:30:00"),
        _raw_candle(candle_date_time_utc="2026-01-01T00:00:00"),
    ]

    with pytest.raises(UpbitResponseError):
        await _get_candles(response, count=1)


@pytest.mark.parametrize(("to", "count"), [(datetime(2026, 1, 1, 1, 0), 200), (TO, 0), (TO, 201)])
async def test_get_candles_rejects_invalid_arguments(to: datetime, count: int) -> None:
    client = UpbitClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])))
    await client.open()
    try:
        with pytest.raises(ValueError):
            await client.get_candles("KRW-BTC", Timeframe.MINUTE_30, to, count)
    finally:
        await client.close()


async def test_get_candles_rejects_duplicate_candles() -> None:
    response = [
        _raw_candle(candle_date_time_utc="2026-01-01T00:30:00"),
        _raw_candle(candle_date_time_utc="2026-01-01T00:30:00"),
    ]

    with pytest.raises(UpbitResponseError):
        await _get_candles(response)


@pytest.mark.parametrize(
    "to",
    [
        # 캔들 경계가 아니면 진행 중인 캔들이 섞인다
        datetime(2026, 1, 1, 1, 0, 30, tzinfo=UTC),
        datetime(2026, 1, 1, 1, 15, tzinfo=UTC),
        # 아직 오지 않은 마감 시각
        datetime(2026, 1, 1, 1, 30, tzinfo=UTC),
    ],
)
async def test_get_candles_rejects_unsafe_to(to: datetime) -> None:
    now = datetime(2026, 1, 1, 1, 10, tzinfo=UTC)
    client = UpbitClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])), clock=lambda: now)
    await client.open()
    try:
        with pytest.raises(ValueError, match="past 30m candle boundary"):
            await client.get_candles("KRW-BTC", Timeframe.MINUTE_30, to)
    finally:
        await client.close()


async def test_get_candles_accepts_boundary_that_just_passed() -> None:
    now = datetime(2026, 1, 1, 1, 0, 0, 60_000, tzinfo=UTC)
    client = UpbitClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])), clock=lambda: now)
    await client.open()
    try:
        assert await client.get_candles("KRW-BTC", Timeframe.MINUTE_30, TO) == []
    finally:
        await client.close()


async def test_async_with_opens_and_closes() -> None:
    client = UpbitClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True})))

    async with client as opened:
        assert opened is client
        assert await client._request("GET", "/v1/test") == {"ok": True}

    with pytest.raises(RuntimeError, match="not open"):
        await client._request("GET", "/v1/test")


async def test_get_candles_waits_for_rate_limiter() -> None:
    events: list[str] = []

    class RecordingLimiter(RateLimiter):
        async def acquire(self) -> None:
            events.append("acquire")

    def handler(request: httpx.Request) -> httpx.Response:
        events.append("request")
        return httpx.Response(200, json=[])

    async with UpbitClient(transport=httpx.MockTransport(handler), candle_limiter=RecordingLimiter(1)) as client:
        await client.get_candles("KRW-BTC", Timeframe.MINUTE_30, TO)

    assert events == ["acquire", "request"]
