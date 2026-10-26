import asyncio
import os
import signal
from pathlib import Path

import pytest

from src.application import Application, Job

STOP_TIMEOUT = 1.0


async def _start(app: Application) -> asyncio.Task[None]:
    task = asyncio.create_task(app.run())
    # run()이 신호 처리기를 등록하고 대기에 들어갈 때까지 진행시킨다
    await asyncio.sleep(0)
    assert not task.done()
    return task


async def test_run_stops_when_stop_called() -> None:
    app = Application(Path("config.json"))
    task = await _start(app)

    app.stop()

    await asyncio.wait_for(task, STOP_TIMEOUT)


async def test_stop_called_twice_stops_once() -> None:
    app = Application(Path("config.json"))
    task = await _start(app)

    app.stop()
    app.stop()

    await asyncio.wait_for(task, STOP_TIMEOUT)


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
async def test_run_stops_on_signal(sig: signal.Signals) -> None:
    app = Application(Path("config.json"))
    task = await _start(app)

    os.kill(os.getpid(), sig)

    await asyncio.wait_for(task, STOP_TIMEOUT)


async def test_signal_handlers_removed_after_run() -> None:
    app = Application(Path("config.json"))
    task = await _start(app)
    app.stop()
    await asyncio.wait_for(task, STOP_TIMEOUT)

    loop = asyncio.get_running_loop()
    # 이미 제거된 처리기는 다시 제거할 수 없다
    assert not loop.remove_signal_handler(signal.SIGINT)
    assert not loop.remove_signal_handler(signal.SIGTERM)


async def test_stop_before_run_returns_immediately() -> None:
    app = Application(Path("config.json"))
    app.stop()

    await asyncio.wait_for(app.run(), STOP_TIMEOUT)


def _hello(market: str, done: asyncio.Event | None = None) -> Job:
    async def job() -> None:
        print(f"hello world {market}")
        if done is not None:
            done.set()

    return job


async def _wait_all(*events: asyncio.Event) -> None:
    """작업이 모두 끝날 때까지 기다린다. 종료 요청 전에 시작하지 않은 작업은 건너뛰기 때문이다."""
    await asyncio.wait_for(asyncio.gather(*(e.wait() for e in events)), STOP_TIMEOUT)


async def _stop_and_wait(app: Application, task: asyncio.Task[None]) -> None:
    app.stop()
    await asyncio.wait_for(task, STOP_TIMEOUT)


async def test_submitted_jobs_run(capsys: pytest.CaptureFixture[str]) -> None:
    app = Application(Path("config.json"))
    task = await _start(app)

    btc_done, eth_done = asyncio.Event(), asyncio.Event()

    assert app.submit("KRW-BTC", _hello("KRW-BTC", btc_done))
    assert app.submit("KRW-ETH", _hello("KRW-ETH", eth_done))
    await _wait_all(btc_done, eth_done)
    await _stop_and_wait(app, task)

    out = capsys.readouterr().out
    assert "hello world KRW-BTC" in out
    assert "hello world KRW-ETH" in out


async def test_jobs_of_different_markets_run_concurrently(capsys: pytest.CaptureFixture[str]) -> None:
    app = Application(Path("config.json"))
    task = await _start(app)
    both_started = asyncio.Barrier(2)

    def job(market: str, done: asyncio.Event) -> Job:
        async def run() -> None:
            # 두 마켓이 동시에 실행 중이어야 둘 다 이 지점을 지난다
            await asyncio.wait_for(both_started.wait(), STOP_TIMEOUT)
            print(f"hello world {market}")
            done.set()

        return run

    btc_done, eth_done = asyncio.Event(), asyncio.Event()
    app.submit("KRW-BTC", job("KRW-BTC", btc_done))
    app.submit("KRW-ETH", job("KRW-ETH", eth_done))
    await _wait_all(btc_done, eth_done)
    await _stop_and_wait(app, task)

    out = capsys.readouterr().out
    assert "hello world KRW-BTC" in out
    assert "hello world KRW-ETH" in out


async def test_jobs_of_same_market_run_one_at_a_time_in_order(capsys: pytest.CaptureFixture[str]) -> None:
    app = Application(Path("config.json"))
    task = await _start(app)

    second_done = asyncio.Event()

    def job(name: str) -> Job:
        async def run() -> None:
            print(f"start {name}")
            await asyncio.sleep(0.01)
            print(f"end {name}")
            if name == "second":
                second_done.set()

        return run

    app.submit("KRW-BTC", job("first"))
    app.submit("KRW-BTC", job("second"))
    await _wait_all(second_done)
    await _stop_and_wait(app, task)

    assert capsys.readouterr().out.splitlines() == ["start first", "end first", "start second", "end second"]


async def test_failed_job_does_not_stop_other_jobs(capsys: pytest.CaptureFixture[str]) -> None:
    app = Application(Path("config.json"))
    task = await _start(app)

    async def broken() -> None:
        raise RuntimeError("boom")

    btc_done, eth_done = asyncio.Event(), asyncio.Event()
    app.submit("KRW-BTC", broken)
    app.submit("KRW-BTC", _hello("KRW-BTC", btc_done))
    app.submit("KRW-ETH", _hello("KRW-ETH", eth_done))
    await _wait_all(btc_done, eth_done)

    assert not task.done()
    await _stop_and_wait(app, task)
    out = capsys.readouterr().out
    assert "hello world KRW-BTC" in out
    assert "hello world KRW-ETH" in out


async def test_submit_rejected_before_run() -> None:
    app = Application(Path("config.json"))

    assert not app.submit("KRW-BTC", _hello("KRW-BTC"))


async def test_submit_rejected_after_stop(capsys: pytest.CaptureFixture[str]) -> None:
    app = Application(Path("config.json"))
    task = await _start(app)
    await _stop_and_wait(app, task)

    assert not app.submit("KRW-BTC", _hello("KRW-BTC"))
    await asyncio.sleep(0)
    assert capsys.readouterr().out == ""


async def test_stop_waits_for_running_job(capsys: pytest.CaptureFixture[str]) -> None:
    app = Application(Path("config.json"))
    task = await _start(app)

    async def slow() -> None:
        await asyncio.sleep(0.05)
        print("hello world done")

    app.submit("KRW-BTC", slow)
    await asyncio.sleep(0)
    await _stop_and_wait(app, task)

    assert "hello world done" in capsys.readouterr().out


async def test_stop_cancels_job_after_shutdown_timeout(capsys: pytest.CaptureFixture[str]) -> None:
    app = Application(Path("config.json"), shutdown_timeout=0.01)
    task = await _start(app)

    async def stuck() -> None:
        await asyncio.sleep(10)
        print("hello world never")

    app.submit("KRW-BTC", stuck)
    await asyncio.sleep(0)
    await _stop_and_wait(app, task)

    assert "hello world never" not in capsys.readouterr().out


async def test_queued_job_of_same_market_skipped_after_stop(capsys: pytest.CaptureFixture[str]) -> None:
    app = Application(Path("config.json"))
    task = await _start(app)
    first_started = asyncio.Event()

    async def first() -> None:
        first_started.set()
        await asyncio.sleep(0.01)
        print("hello world first")

    app.submit("KRW-BTC", first)
    app.submit("KRW-BTC", _hello("second"))
    await first_started.wait()
    await _stop_and_wait(app, task)

    out = capsys.readouterr().out
    assert "hello world first" in out
    assert "hello world second" not in out


async def test_submit_rejected_right_after_stop_requested() -> None:
    app = Application(Path("config.json"))
    task = await _start(app)

    app.stop()

    assert not app.submit("KRW-BTC", _hello("KRW-BTC"))
    await asyncio.wait_for(task, STOP_TIMEOUT)


async def test_stop_finishes_even_if_job_ignores_cancel() -> None:
    app = Application(Path("config.json"), shutdown_timeout=0.01, cancel_timeout=0.01)
    task = await _start(app)

    async def ignores_cancel() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            # 취소를 무시하고 계속 실행되는 작업
            await asyncio.sleep(10)

    app.submit("KRW-BTC", ignores_cancel)
    await asyncio.sleep(0)

    await _stop_and_wait(app, task)

    # 테스트가 끝난 뒤 남지 않도록 정리한다
    for job_task in asyncio.all_tasks() - {asyncio.current_task()}:
        job_task.cancel()
