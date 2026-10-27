import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest

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
