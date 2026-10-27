import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable


class RateLimiter:
    """period초 동안 최대 limit번만 통과시킨다. 넘으면 자리가 날 때까지 기다린다."""

    def __init__(
        self,
        limit: int,
        period: float = 1.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if limit < 1 or period <= 0:
            raise ValueError("limit must be at least 1 and period must be positive")
        self._limit = limit
        self._period = period
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[float] = deque()
        # 여러 작업이 동시에 기다릴 때 한 번에 하나씩 자리를 확인해, 함께 깨어나 한도를 넘지 않게 한다
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                while self._sent and now - self._sent[0] >= self._period:
                    self._sent.popleft()
                if len(self._sent) < self._limit:
                    self._sent.append(now)
                    return
                await self._sleep(self._period - (now - self._sent[0]))
