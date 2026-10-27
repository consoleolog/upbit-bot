import asyncio

import pytest

from src.rate_limiter import RateLimiter


class FakeTime:
    """sleep을 호출하면 그만큼 시간이 흐르는 가짜 시계."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _limiter(fake: FakeTime, limit: int = 3) -> RateLimiter:
    return RateLimiter(limit, 1.0, clock=fake.clock, sleep=fake.sleep)


async def test_passes_without_waiting_within_limit() -> None:
    fake = FakeTime()
    limiter = _limiter(fake)

    for _ in range(3):
        await limiter.acquire()

    assert fake.slept == []


async def test_waits_until_oldest_request_leaves_window() -> None:
    fake = FakeTime()
    limiter = _limiter(fake)
    for at in (0.0, 0.2, 0.4):
        fake.now = at
        await limiter.acquire()

    fake.now = 0.5
    await limiter.acquire()

    # 0.0초의 요청이 1초 창에서 빠지는 1.0초까지 기다린다
    assert fake.slept == [pytest.approx(0.5)]
    assert fake.now == pytest.approx(1.0)


async def test_window_frees_up_over_time() -> None:
    fake = FakeTime()
    limiter = _limiter(fake)
    for _ in range(3):
        await limiter.acquire()

    fake.now = 1.0
    await limiter.acquire()

    assert fake.slept == []


async def test_concurrent_requests_never_exceed_limit() -> None:
    fake = FakeTime()
    limiter = _limiter(fake)
    passed_at: list[float] = []

    async def request() -> None:
        await limiter.acquire()
        passed_at.append(fake.now)

    await asyncio.gather(*(request() for _ in range(7)))

    assert len(passed_at) == 7
    # 어느 1초 창에도 3개를 넘지 않는다
    for start in passed_at:
        assert sum(1 for t in passed_at if start <= t < start + 1.0) <= 3


@pytest.mark.parametrize(("limit", "period"), [(0, 1.0), (1, 0.0), (1, -1.0)])
def test_invalid_arguments_rejected(limit: int, period: float) -> None:
    with pytest.raises(ValueError, match="limit"):
        RateLimiter(limit, period)
