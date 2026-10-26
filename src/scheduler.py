import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

from src.application import Job
from src.config import Timeframe

logger = logging.getLogger(__name__)

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

Submit = Callable[[str, Job], bool]
JobFactory = Callable[[str, datetime], Job]
Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]


def utc_now() -> datetime:
    return datetime.now(UTC)


def next_candle_close(now: datetime, timeframe: Timeframe) -> datetime:
    """now 이후 처음으로 캔들이 마감되는 시각. 캔들 경계는 UTC 기준으로 timeframe마다 나뉜다고 가정한다."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    elapsed = now - EPOCH
    periods = elapsed // timeframe.duration
    return EPOCH + (periods + 1) * timeframe.duration


class CandleScheduler:
    """timeframe의 캔들이 마감될 때마다 마켓별 작업을 넘긴다."""

    def __init__(
        self,
        *,
        timeframe: Timeframe,
        markets: Sequence[str],
        submit: Submit,
        job_factory: JobFactory,
        clock: Clock = utc_now,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._timeframe = timeframe
        self._markets = tuple(markets)
        self._submit = submit
        self._job_factory = job_factory
        self._clock = clock
        self._sleep = sleep

    async def run(self) -> None:
        """취소될 때까지 캔들 마감마다 작업을 넘긴다. 같은 마감 시각의 작업은 한 번만 넘긴다."""
        last_close: datetime | None = None
        while True:
            # 매번 현재 시각에서 다음 마감을 다시 계산한다. 늦게 깨어나 놓친 마감은 건너뛰고,
            # 놓친 캔들은 작업 쪽의 놓친 캔들 규칙(매매 로직 3.0)으로 처리한다
            close = next_candle_close(self._clock(), self._timeframe)
            if last_close is not None and close <= last_close:
                # 시계가 뒤로 조정되면 이미 넘긴 마감이 다시 계산될 수 있다
                close = last_close + self._timeframe.duration
            await self._sleep_until(close)
            logger.info("candle closed (timeframe=%s, close=%s)", self._timeframe, close.isoformat())
            for market in self._markets:
                self._submit(market, self._job_factory(market, close))
            last_close = close

    async def _sleep_until(self, close: datetime) -> None:
        # sleep은 단조 시계로 재므로 벽시계가 마감 시각에 닿기 전에 깨어날 수 있다.
        # 닿을 때까지 다시 기다려, 마감되지 않은 캔들의 작업을 넘기지 않는다
        while (remaining := (close - self._clock()).total_seconds()) > 0:
            await self._sleep(remaining)
