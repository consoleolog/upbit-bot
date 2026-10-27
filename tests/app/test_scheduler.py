import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from src.app.application import Job
from src.app.config import Timeframe
from src.app.scheduler import CandleScheduler, next_candle_close


@pytest.mark.parametrize(
    ("now", "timeframe", "expected"),
    [
        (datetime(2026, 1, 1, 0, 10, tzinfo=UTC), Timeframe.MINUTE_30, datetime(2026, 1, 1, 0, 30, tzinfo=UTC)),
        # 정확히 경계에 있으면 지금 마감된 캔들이 아니라 다음 마감을 돌려준다
        (datetime(2026, 1, 1, 0, 30, tzinfo=UTC), Timeframe.MINUTE_30, datetime(2026, 1, 1, 1, 0, tzinfo=UTC)),
        (datetime(2026, 1, 1, 0, 59, 59, tzinfo=UTC), Timeframe.MINUTE_1, datetime(2026, 1, 1, 1, 0, tzinfo=UTC)),
        (datetime(2026, 1, 1, 5, 0, tzinfo=UTC), Timeframe.MINUTE_240, datetime(2026, 1, 1, 8, 0, tzinfo=UTC)),
    ],
)
def test_next_candle_close(now: datetime, timeframe: Timeframe, expected: datetime) -> None:
    assert next_candle_close(now, timeframe) == expected


def test_next_candle_close_with_other_timezone() -> None:
    kst = datetime(2026, 1, 1, 9, 10, tzinfo=UTC).astimezone(datetime.now().astimezone().tzinfo)

    assert next_candle_close(kst, Timeframe.MINUTE_30) == datetime(2026, 1, 1, 9, 30, tzinfo=UTC)


def test_next_candle_close_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        next_candle_close(datetime(2026, 1, 1, 0, 10), Timeframe.MINUTE_30)


class FakeClock:
    """sleep을 호출하면 그만큼 시간이 흐르는 가짜 시계. 정해진 횟수 뒤에는 멈춘다(취소)."""

    def __init__(self, start: datetime, ticks: int) -> None:
        self.now = start
        self.ticks = ticks
        self.slept: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        if len(self.slept) == self.ticks:
            raise asyncio.CancelledError
        self.slept.append(seconds)
        self.now += timedelta(seconds=seconds)


async def test_scheduler_submits_hello_world_job_per_market_at_each_close(capsys: pytest.CaptureFixture[str]) -> None:
    clock = FakeClock(datetime(2026, 1, 1, 0, 10, tzinfo=UTC), ticks=2)
    submitted: list[tuple[str, Job]] = []

    def hello(market: str, close: datetime) -> Job:
        async def job() -> None:
            print(f"hello world {market} {close:%H:%M}")

        return job

    scheduler = CandleScheduler(
        timeframe=Timeframe.MINUTE_30,
        markets=["KRW-BTC", "KRW-ETH"],
        submit=lambda market, job: submitted.append((market, job)) is None,
        job_factory=hello,
        clock=clock,
        sleep=clock.sleep,
    )

    with pytest.raises(asyncio.CancelledError):
        await scheduler.run()
    for _, job in submitted:
        await job()

    # 00:10 → 00:30까지 20분, 이후 30분마다
    assert clock.slept == [20 * 60, 30 * 60]
    assert capsys.readouterr().out.splitlines() == [
        "hello world KRW-BTC 00:30",
        "hello world KRW-ETH 00:30",
        "hello world KRW-BTC 01:00",
        "hello world KRW-ETH 01:00",
    ]


async def test_scheduler_skips_closes_missed_while_late() -> None:
    start = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)
    closes: list[datetime] = []
    late = timedelta(hours=2)

    async def late_sleep(seconds: float) -> None:
        # 잠든 사이 시계가 2시간 늦게 깨어난 상황
        nonlocal now
        if len(closes) == 2:
            raise asyncio.CancelledError
        now += timedelta(seconds=seconds) + (late if not closes else timedelta())

    now = start

    def record(market: str, close: datetime) -> Job:
        closes.append(close)

        async def job() -> None:
            return None

        return job

    scheduler = CandleScheduler(
        timeframe=Timeframe.MINUTE_30,
        markets=["KRW-BTC"],
        submit=lambda market, job: True,
        job_factory=record,
        clock=lambda: now,
        sleep=late_sleep,
    )

    with pytest.raises(asyncio.CancelledError):
        await scheduler.run()

    # 첫 마감(00:30) 뒤 2시간 늦게 깨어나면, 그 사이의 마감은 건너뛰고 현재 시각 이후의 마감(03:00)으로 넘어간다
    assert closes == [datetime(2026, 1, 1, 0, 30, tzinfo=UTC), datetime(2026, 1, 1, 3, 0, tzinfo=UTC)]


async def test_scheduler_waits_again_when_woken_before_close() -> None:
    # 단조 시계와 벽시계가 어긋나 마감 직전에 깨어나는 상황
    now = datetime(2026, 1, 1, 0, 29, 0, tzinfo=UTC)
    closes: list[datetime] = []
    submitted_at: list[datetime] = []

    async def early_sleep(seconds: float) -> None:
        nonlocal now
        if len(closes) == 2:
            raise asyncio.CancelledError
        # 요청보다 0.1초 일찍 깨어난다. 남은 시간이 0.1초 이하면 그만큼만 흐른다
        now += timedelta(seconds=max(seconds - 0.1, min(seconds, 0.1)))

    def record(market: str, close: datetime) -> Job:
        closes.append(close)
        submitted_at.append(now)

        async def job() -> None:
            return None

        return job

    scheduler = CandleScheduler(
        timeframe=Timeframe.MINUTE_1,
        markets=["KRW-BTC"],
        submit=lambda market, job: True,
        job_factory=record,
        clock=lambda: now,
        sleep=early_sleep,
    )

    with pytest.raises(asyncio.CancelledError):
        await scheduler.run()

    # 같은 마감을 두 번 넘기지 않고, 마감 시각이 지난 뒤에만 넘긴다
    assert closes == [datetime(2026, 1, 1, 0, 30, tzinfo=UTC), datetime(2026, 1, 1, 0, 31, tzinfo=UTC)]
    assert all(at >= close for at, close in zip(submitted_at, closes, strict=True))


async def test_scheduler_does_not_repeat_close_when_clock_moves_back() -> None:
    now = datetime(2026, 1, 1, 0, 29, 30, tzinfo=UTC)
    closes: list[datetime] = []

    async def sleep(seconds: float) -> None:
        nonlocal now
        if len(closes) == 2:
            raise asyncio.CancelledError
        now += timedelta(seconds=seconds)

    def record(market: str, close: datetime) -> Job:
        nonlocal now
        closes.append(close)
        # 첫 마감 직후 시계가 1분 뒤로 조정된다
        if len(closes) == 1:
            now -= timedelta(minutes=1)

        async def job() -> None:
            return None

        return job

    scheduler = CandleScheduler(
        timeframe=Timeframe.MINUTE_1,
        markets=["KRW-BTC"],
        submit=lambda market, job: True,
        job_factory=record,
        clock=lambda: now,
        sleep=sleep,
    )

    with pytest.raises(asyncio.CancelledError):
        await scheduler.run()

    assert closes == [datetime(2026, 1, 1, 0, 30, tzinfo=UTC), datetime(2026, 1, 1, 0, 31, tzinfo=UTC)]
