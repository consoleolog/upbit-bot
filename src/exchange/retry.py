"""거래소 조회 재시도. 조회는 다시 보내도 거래소의 상태가 바뀌지 않으므로, 일시적인 실패만 몇 번 다시 시도한다.

주문에는 쓰지 않는다. 주문은 응답을 받지 못해도 다시 보내지 않고 identifier로 결과를 조회한다 (매매 로직 5.1).
다시 해도 결과가 같은 실패(인증·권한, 잘못된 요청, 응답 형식 오류, IP 차단)는 바로 내보낸다.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

from src.exchange.upbit_client import UpbitConnectionError, UpbitRateLimitError, UpbitServerError, UpbitTimeoutError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 다시 시도하는 실패: 연결 실패(DNS 등, 요청이 거래소에 가지 않음), 시간 초과, 거래소 장애(5xx), 요청 수 제한(429)
RETRYABLE_READ_ERRORS = (UpbitConnectionError, UpbitTimeoutError, UpbitServerError, UpbitRateLimitError)
# 다시 시도하기 전 대기(초). 길이만큼 다시 시도한다 (처음 포함 3번). 요청마다 시간 제한이 있어 전체 시간도 한정된다
READ_RETRY_DELAYS = (1.0, 2.0)


async def retry_read(
    call: Callable[[], Awaitable[T]],
    *,
    what: str,
    delays: Sequence[float] = READ_RETRY_DELAYS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """조회 call을 실행하고, 일시적인 실패면 delays만큼 기다렸다가 다시 시도한다. 마지막 시도의 실패는 그대로 내보낸다."""
    attempts = len(delays) + 1
    for attempt, delay in enumerate(delays, start=1):
        try:
            return await call()
        except RETRYABLE_READ_ERRORS as error:
            # 오류 메시지 대신 종류만 남긴다 (요청 경로 등이 길게 섞이지 않게)
            logger.warning(
                "%s failed (%s); retrying in %ss (attempt %d/%d)", what, type(error).__name__, delay, attempt, attempts
            )
            await sleep(delay)
    return await call()
