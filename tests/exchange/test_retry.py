import pytest

from src.exchange.retry import READ_RETRY_DELAYS, retry_read
from src.exchange.upbit_client import (
    UpbitAuthError,
    UpbitBlockedError,
    UpbitConnectionError,
    UpbitRateLimitError,
    UpbitRequestError,
    UpbitResponseError,
    UpbitServerError,
    UpbitTimeoutError,
)


class Calls:
    """정해 둔 결과(예외 또는 값)를 차례로 돌려주는 조회. 대기 시간도 기록한다."""

    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.count = 0
        self.sleeps: list[float] = []

    async def call(self) -> object:
        self.count += 1
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


async def test_success_does_not_wait() -> None:
    calls = Calls("ok")

    assert await retry_read(calls.call, what="fetch", sleep=calls.sleep) == "ok"
    assert (calls.count, calls.sleeps) == (1, [])


@pytest.mark.parametrize(
    "error",
    [
        UpbitConnectionError("GET /v1/candles failed to connect: ConnectError"),
        UpbitTimeoutError("GET /v1/candles timed out after 10s"),
        UpbitServerError("GET /v1/candles failed (503)", status=503),
        UpbitRateLimitError("GET /v1/candles failed (429)", status=429),
    ],
    ids=lambda error: type(error).__name__,
)
async def test_temporary_failure_is_retried(error: Exception, caplog: pytest.LogCaptureFixture) -> None:
    calls = Calls(error, "ok")

    assert await retry_read(calls.call, what="candle fetch (market=KRW-BTC)", sleep=calls.sleep) == "ok"

    assert (calls.count, calls.sleeps) == (2, [READ_RETRY_DELAYS[0]])
    assert (
        f"candle fetch (market=KRW-BTC) failed ({type(error).__name__}); retrying in 1.0s (attempt 1/3)" in caplog.text
    )
    # 오류 메시지(요청 경로 등)는 남기지 않는다
    assert "/v1/candles" not in caplog.text


async def test_gives_up_after_last_attempt_without_waiting() -> None:
    last = UpbitConnectionError("third")
    calls = Calls(UpbitConnectionError("first"), UpbitConnectionError("second"), last)

    with pytest.raises(UpbitConnectionError) as error:
        await retry_read(calls.call, what="fetch", sleep=calls.sleep)

    # 마지막 실패는 그대로 내보내고, 그 뒤에는 기다리지 않는다
    assert error.value is last
    assert (calls.count, calls.sleeps) == (3, [1.0, 2.0])


@pytest.mark.parametrize(
    "error",
    [
        UpbitAuthError("401 jwt_verification", status=401),
        UpbitRequestError("400 invalid_parameter", status=400),
        UpbitResponseError("unexpected candle response"),
        UpbitBlockedError("418", status=418),
        ValueError("bug"),
    ],
    ids=lambda error: type(error).__name__,
)
async def test_permanent_failure_is_not_retried(error: Exception) -> None:
    calls = Calls(error, "ok")

    # 다시 해도 결과가 같은 실패는 바로 내보낸다 (원인을 가리지 않음)
    with pytest.raises(type(error)):
        await retry_read(calls.call, what="fetch", sleep=calls.sleep)

    assert (calls.count, calls.sleeps) == (1, [])


async def test_no_delays_means_single_attempt() -> None:
    calls = Calls(UpbitTimeoutError("t"), "ok")

    with pytest.raises(UpbitTimeoutError):
        await retry_read(calls.call, what="fetch", delays=(), sleep=calls.sleep)

    assert (calls.count, calls.sleeps) == (1, [])
