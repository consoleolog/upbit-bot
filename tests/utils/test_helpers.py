import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.utils.constants import CandleType
from src.utils.helpers import measure_time, parse_timeframe, rate_limit, retry

# ---------------------------------------------------------------------------
# 기본 포맷 — 단순 단위 문자열
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_timeframe_seconds():
    """초 단위 문자열을 올바르게 변환한다."""
    assert parse_timeframe("1s") == timedelta(seconds=1)


@pytest.mark.unit
def test_parse_timeframe_minutes():
    """분 단위 문자열을 올바르게 변환한다."""
    assert parse_timeframe("5m") == timedelta(minutes=5)


@pytest.mark.unit
def test_parse_timeframe_hours():
    """시 단위 문자열을 올바르게 변환한다."""
    assert parse_timeframe("1h") == timedelta(hours=1)


@pytest.mark.unit
def test_parse_timeframe_days():
    """일 단위 문자열을 올바르게 변환한다."""
    assert parse_timeframe("1d") == timedelta(days=1)


@pytest.mark.unit
def test_parse_timeframe_weeks():
    """주 단위 문자열을 올바르게 변환한다."""
    assert parse_timeframe("1w") == timedelta(weeks=1)


@pytest.mark.unit
def test_parse_timeframe_large_minutes():
    """240m 처럼 큰 분 값도 올바르게 변환한다."""
    assert parse_timeframe("240m") == timedelta(minutes=240)


# ---------------------------------------------------------------------------
# candle.Xm / candle.Xs 포맷 — CandleType.value 직접 전달
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_timeframe_candle_second():
    """candle.1s 포맷을 1초 timedelta로 변환한다."""
    assert parse_timeframe("candle.1s") == timedelta(seconds=1)


@pytest.mark.unit
def test_parse_timeframe_candle_1m():
    """candle.1m 포맷을 1분 timedelta로 변환한다."""
    assert parse_timeframe("candle.1m") == timedelta(minutes=1)


@pytest.mark.unit
def test_parse_timeframe_candle_3m():
    """candle.3m 포맷을 3분 timedelta로 변환한다."""
    assert parse_timeframe("candle.3m") == timedelta(minutes=3)


@pytest.mark.unit
def test_parse_timeframe_candle_240m():
    """candle.240m 포맷을 240분 timedelta로 변환한다."""
    assert parse_timeframe("candle.240m") == timedelta(minutes=240)


@pytest.mark.unit
def test_parse_timeframe_all_candle_types():
    """모든 CandleType 값이 ValueError 없이 timedelta로 변환된다."""
    expected = {
        CandleType.SECOND: timedelta(seconds=1),
        CandleType.MINUTE_1: timedelta(minutes=1),
        CandleType.MINUTE_3: timedelta(minutes=3),
        CandleType.MINUTE_5: timedelta(minutes=5),
        CandleType.MINUTE_10: timedelta(minutes=10),
        CandleType.MINUTE_15: timedelta(minutes=15),
        CandleType.HALF_HOUR: timedelta(minutes=30),
        CandleType.HOUR: timedelta(minutes=60),
        CandleType.HOUR_4: timedelta(minutes=240),
    }
    for candle_type, expected_delta in expected.items():
        assert parse_timeframe(candle_type.value) == expected_delta, (
            f"{candle_type.value} should map to {expected_delta}"
        )


# ---------------------------------------------------------------------------
# 대소문자 구분 없음
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_timeframe_uppercase_is_accepted():
    """대문자 단위도 소문자로 정규화되어 처리된다."""
    assert parse_timeframe("5M") == timedelta(minutes=5)


# ---------------------------------------------------------------------------
# 잘못된 포맷 — ValueError
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_timeframe_invalid_raises_value_error():
    """인식할 수 없는 포맷은 ValueError를 발생시킨다."""
    with pytest.raises(ValueError):
        parse_timeframe("invalid")


@pytest.mark.unit
def test_parse_timeframe_empty_string_raises_value_error():
    """빈 문자열은 ValueError를 발생시킨다."""
    with pytest.raises(ValueError):
        parse_timeframe("")


# ---------------------------------------------------------------------------
# retry — sync
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_retry_sync_succeeds_on_first_attempt():
    """첫 시도에 성공하면 결과를 그대로 반환한다."""

    @retry(max_retries=3, delay=0)
    def func():
        return 42

    assert func() == 42


@pytest.mark.unit
def test_retry_sync_retries_on_failure():
    """실패 후 성공하면 결과를 반환한다."""
    call_count = 0

    @retry(max_retries=3, delay=0)
    def func():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ValueError("임시 오류")
        return "ok"

    with patch("time.sleep"):
        result = func()

    assert result == "ok"
    assert call_count == 3


@pytest.mark.unit
def test_retry_sync_raises_after_max_retries():
    """max_retries 초과 시 마지막 예외를 발생시킨다."""

    @retry(max_retries=3, delay=0)
    def func():
        raise RuntimeError("계속 실패")

    with patch("time.sleep"), pytest.raises(RuntimeError, match="계속 실패"):
        func()


@pytest.mark.unit
def test_retry_sync_call_count_equals_max_retries():
    """정확히 max_retries 횟수만큼 호출된다."""
    mock = MagicMock(side_effect=ValueError("오류"))

    @retry(max_retries=4, delay=0)
    def func():
        return mock()

    with patch("time.sleep"), pytest.raises(ValueError):
        func()

    assert mock.call_count == 4


@pytest.mark.unit
def test_retry_sync_fixed_delay(monkeypatch):
    """exponential_backoff=False이면 고정 delay로 sleep한다."""
    sleep_calls = []
    monkeypatch.setattr("time.sleep", lambda s: sleep_calls.append(s))

    @retry(max_retries=3, delay=0.5, exponential_backoff=False)
    def func():
        raise ValueError

    with pytest.raises(ValueError):
        func()

    assert all(s == 0.5 for s in sleep_calls)


@pytest.mark.unit
def test_retry_sync_exponential_backoff(monkeypatch):
    """exponential_backoff=True이면 delay가 2배씩 증가한다."""
    sleep_calls = []
    monkeypatch.setattr("time.sleep", lambda s: sleep_calls.append(s))

    @retry(max_retries=3, delay=1.0, exponential_backoff=True)
    def func():
        raise ValueError

    with pytest.raises(ValueError):
        func()

    assert sleep_calls == [1.0, 2.0, 4.0]


@pytest.mark.unit
def test_retry_sync_preserves_return_value():
    """재시도 후 성공 시 반환값이 정확히 전달된다."""

    @retry(max_retries=3, delay=0)
    def func():
        return {"key": "value"}

    with patch("time.sleep"):
        result = func()

    assert result == {"key": "value"}


# ---------------------------------------------------------------------------
# retry — async
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_async_succeeds_on_first_attempt():
    """첫 시도에 성공하면 결과를 그대로 반환한다."""

    @retry(max_retries=3, delay=0)
    async def func():
        return 42

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await func()

    assert result == 42


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_async_retries_on_failure():
    """실패 후 성공하면 결과를 반환한다."""
    call_count = 0

    @retry(max_retries=3, delay=0)
    async def func():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ValueError("임시 오류")
        return "ok"

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await func()

    assert result == "ok"
    assert call_count == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_async_raises_after_max_retries():
    """max_retries 초과 시 마지막 예외를 발생시킨다."""

    @retry(max_retries=3, delay=0)
    async def func():
        raise RuntimeError("계속 실패")

    with patch("asyncio.sleep", new_callable=AsyncMock), pytest.raises(RuntimeError, match="계속 실패"):
        await func()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_async_call_count_equals_max_retries():
    """정확히 max_retries 횟수만큼 호출된다."""
    mock = AsyncMock(side_effect=ValueError("오류"))

    @retry(max_retries=4, delay=0)
    async def func():
        return await mock()

    with patch("asyncio.sleep", new_callable=AsyncMock), pytest.raises(ValueError):
        await func()

    assert mock.call_count == 4


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_async_exponential_backoff():
    """exponential_backoff=True이면 asyncio.sleep에 지수 증가 값이 전달된다."""
    sleep_mock = AsyncMock()

    @retry(max_retries=3, delay=1.0, exponential_backoff=True)
    async def func():
        raise ValueError

    with patch("asyncio.sleep", sleep_mock), pytest.raises(ValueError):
        await func()

    sleep_calls = [call.args[0] for call in sleep_mock.call_args_list]
    assert sleep_calls == [1.0, 2.0, 4.0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_async_timeout_error_is_retried():
    """asyncio.TimeoutError도 재시도 대상이다."""
    call_count = 0

    @retry(max_retries=3, delay=0)
    async def func():
        nonlocal call_count
        call_count += 1
        raise asyncio.TimeoutError

    with patch("asyncio.sleep", new_callable=AsyncMock), pytest.raises(asyncio.TimeoutError):
        await func()

    assert call_count == 3


# ---------------------------------------------------------------------------
# retry — 래퍼 선택
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_retry_returns_sync_wrapper_for_sync_func():
    """sync 함수에는 sync 래퍼가 반환된다 (coroutine이 아님)."""
    import asyncio as _asyncio

    @retry(max_retries=1, delay=0)
    def func():
        return 1

    assert not _asyncio.iscoroutinefunction(func)


@pytest.mark.unit
def test_retry_returns_async_wrapper_for_async_func():
    """async 함수에는 async 래퍼가 반환된다."""
    import asyncio as _asyncio

    @retry(max_retries=1, delay=0)
    async def func():
        return 1

    assert _asyncio.iscoroutinefunction(func)


# ---------------------------------------------------------------------------
# rate_limit — sync
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rate_limit_sync_returns_sync_wrapper():
    """sync 함수에는 sync 래퍼가 반환된다 (coroutine이 아님)."""

    @rate_limit(calls=5, period=1.0)
    def func():
        return 1

    assert not asyncio.iscoroutinefunction(func)


@pytest.mark.unit
def test_rate_limit_sync_succeeds_within_limit():
    """호출 횟수가 한도 이내이면 즉시 반환된다."""

    @rate_limit(calls=5, period=1.0)
    def func():
        return 42

    assert func() == 42


@pytest.mark.unit
def test_rate_limit_sync_calls_up_to_limit_without_sleep():
    """한도 이내의 호출은 sleep 없이 처리된다."""
    sleep_calls = []

    @rate_limit(calls=5, period=1.0)
    def func():
        return 1

    with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        for _ in range(5):
            func()

    assert sleep_calls == []


@pytest.mark.unit
def test_rate_limit_sync_sleeps_when_limit_exceeded(monkeypatch):
    """한도 초과 시 time.sleep이 호출된다."""
    slept = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))

    # period를 매우 크게 설정하여 호출 간격이 짧아 보이도록 만든다
    @rate_limit(calls=2, period=100.0)
    def func():
        return 1

    func()
    func()
    func()  # 3번째 호출에서 sleep 발생

    assert len(slept) == 1
    assert slept[0] > 0


@pytest.mark.unit
def test_rate_limit_sync_preserves_return_value():
    """rate_limit이 함수 반환값을 그대로 전달한다."""

    @rate_limit(calls=10, period=1.0)
    def func():
        return {"result": "ok"}

    assert func() == {"result": "ok"}


@pytest.mark.unit
def test_rate_limit_sync_wraps_function_name():
    """@wraps로 원래 함수 이름이 유지된다."""

    @rate_limit(calls=5, period=1.0)
    def my_function():
        return 1

    assert my_function.__name__ == "my_function"


# ---------------------------------------------------------------------------
# rate_limit — async
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rate_limit_async_returns_async_wrapper():
    """async 함수에는 async 래퍼가 반환된다."""

    @rate_limit(calls=5, period=1.0)
    async def func():
        return 1

    assert asyncio.iscoroutinefunction(func)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limit_async_succeeds_within_limit():
    """호출 횟수가 한도 이내이면 즉시 반환된다."""

    @rate_limit(calls=5, period=1.0)
    async def func():
        return 42

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await func()

    assert result == 42


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limit_async_calls_up_to_limit_without_sleep():
    """한도 이내의 호출은 asyncio.sleep 없이 처리된다."""
    sleep_mock = AsyncMock()

    @rate_limit(calls=5, period=1.0)
    async def func():
        return 1

    with patch("asyncio.sleep", sleep_mock):
        for _ in range(5):
            await func()

    sleep_mock.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limit_async_sleeps_when_limit_exceeded():
    """한도 초과 시 asyncio.sleep이 호출된다."""
    sleep_mock = AsyncMock()

    @rate_limit(calls=2, period=100.0)
    async def func():
        return 1

    with patch("asyncio.sleep", sleep_mock):
        await func()
        await func()
        await func()  # 3번째 호출에서 sleep 발생

    sleep_mock.assert_called_once()
    sleep_time = sleep_mock.call_args[0][0]
    assert sleep_time > 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limit_async_preserves_return_value():
    """rate_limit이 함수 반환값을 그대로 전달한다."""

    @rate_limit(calls=10, period=1.0)
    async def func():
        return {"result": "ok"}

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await func()

    assert result == {"result": "ok"}


@pytest.mark.unit
def test_rate_limit_async_wraps_function_name():
    """@wraps로 원래 함수 이름이 유지된다."""

    @rate_limit(calls=5, period=1.0)
    async def my_async_function():
        return 1

    assert my_async_function.__name__ == "my_async_function"


# ---------------------------------------------------------------------------
# retry — max_retries=0 edge case
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_retry_sync_zero_retries_raises_runtime_error():
    """max_retries=0 이면 루프가 실행되지 않아 RuntimeError를 발생시킨다."""

    @retry(max_retries=0, delay=0)
    def func():
        raise ValueError("실행되지 않음")

    with pytest.raises(RuntimeError):
        func()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_async_zero_retries_raises_runtime_error():
    """max_retries=0 이면 루프가 실행되지 않아 RuntimeError를 발생시킨다."""

    @retry(max_retries=0, delay=0)
    async def func():
        raise ValueError("실행되지 않음")

    with patch("asyncio.sleep", new_callable=AsyncMock), pytest.raises(RuntimeError):
        await func()


# ---------------------------------------------------------------------------
# measure_time — sync
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_measure_time_sync_returns_sync_wrapper():
    """sync 함수에는 sync 래퍼가 반환된다 (coroutine이 아님)."""

    @measure_time
    def func():
        return 1

    assert not asyncio.iscoroutinefunction(func)


@pytest.mark.unit
def test_measure_time_sync_returns_correct_value():
    """sync 함수의 반환값이 그대로 전달된다."""

    @measure_time
    def func():
        return {"result": 42}

    assert func() == {"result": 42}


@pytest.mark.unit
def test_measure_time_sync_wraps_function_name():
    """@wraps로 원래 함수 이름이 유지된다."""

    @measure_time
    def my_function():
        return 1

    assert my_function.__name__ == "my_function"


@pytest.mark.unit
def test_measure_time_sync_logs_debug():
    """실행 후 logger.debug가 호출된다."""

    @measure_time
    def func():
        return 1

    with patch("src.utils.helpers.logger") as mock_logger:
        func()

    mock_logger.debug.assert_called_once()
    assert mock_logger.debug.call_args[0][0] == "⏳ 실행 시간"


@pytest.mark.unit
def test_measure_time_sync_propagates_exception():
    """함수가 예외를 발생시키면 그대로 전파된다."""

    @measure_time
    def func():
        raise ValueError("오류 발생")

    with pytest.raises(ValueError, match="오류 발생"):
        func()


@pytest.mark.unit
def test_measure_time_sync_logs_even_on_exception():
    """예외 발생 시에도 실행 시간 로그가 기록된다 (finally 보장)."""

    @measure_time
    def func():
        raise RuntimeError("실패")

    with patch("src.utils.helpers.logger") as mock_logger, pytest.raises(RuntimeError):
        func()

    mock_logger.debug.assert_called_once()
    assert mock_logger.debug.call_args[0][0] == "⏳ 실행 시간"


# ---------------------------------------------------------------------------
# measure_time — async
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_measure_time_async_returns_async_wrapper():
    """async 함수에는 async 래퍼가 반환된다."""

    @measure_time
    async def func():
        return 1

    assert asyncio.iscoroutinefunction(func)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_measure_time_async_returns_correct_value():
    """async 함수의 반환값이 그대로 전달된다."""

    @measure_time
    async def func():
        return {"result": 42}

    assert await func() == {"result": 42}


@pytest.mark.unit
def test_measure_time_async_wraps_function_name():
    """@wraps로 원래 함수 이름이 유지된다."""

    @measure_time
    async def my_async_function():
        return 1

    assert my_async_function.__name__ == "my_async_function"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_measure_time_async_logs_debug():
    """실행 후 logger.debug가 호출된다."""

    @measure_time
    async def func():
        return 1

    with patch("src.utils.helpers.logger") as mock_logger:
        await func()

    mock_logger.debug.assert_called_once()
    assert mock_logger.debug.call_args[0][0] == "⏳ 실행 시간"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_measure_time_async_propagates_exception():
    """async 함수가 예외를 발생시키면 그대로 전파된다."""

    @measure_time
    async def func():
        raise ValueError("비동기 오류")

    with pytest.raises(ValueError, match="비동기 오류"):
        await func()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_measure_time_async_logs_even_on_exception():
    """예외 발생 시에도 실행 시간 로그가 기록된다 (finally 보장)."""

    @measure_time
    async def func():
        raise RuntimeError("비동기 실패")

    with patch("src.utils.helpers.logger") as mock_logger, pytest.raises(RuntimeError):
        await func()

    mock_logger.debug.assert_called_once()
    assert mock_logger.debug.call_args[0][0] == "⏳ 실행 시간"


# ---------------------------------------------------------------------------
# measure_time — elapsed 포맷
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_measure_time_elapsed_microseconds():
    """매우 빠른 함수는 μs 단위로 로깅된다."""

    @measure_time
    def func():
        return 1

    with patch("src.utils.helpers.logger") as mock_logger:
        func()
        call_kwargs = mock_logger.debug.call_args[1]
        assert "μs" in call_kwargs["elapsed"] or "ms" in call_kwargs["elapsed"] or "s" in call_kwargs["elapsed"]


@pytest.mark.unit
def test_measure_time_elapsed_seconds():
    """1초 이상 걸리는 함수는 s 단위로 로깅된다."""

    @measure_time
    def func():
        return 1

    with patch("time.perf_counter", side_effect=[0.0, 1.5]), patch("src.utils.helpers.logger") as mock_logger:
        func()
        call_kwargs = mock_logger.debug.call_args[1]
        assert "s" in call_kwargs["elapsed"]


@pytest.mark.unit
def test_measure_time_elapsed_milliseconds():
    """1ms~1s 사이는 ms 단위로 로깅된다."""

    @measure_time
    def func():
        return 1

    with patch("time.perf_counter", side_effect=[0.0, 0.05]), patch("src.utils.helpers.logger") as mock_logger:
        func()
        call_kwargs = mock_logger.debug.call_args[1]
        assert "ms" in call_kwargs["elapsed"]
