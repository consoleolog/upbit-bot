import asyncio
import os
import signal
from pathlib import Path

import pytest

from src.application import Application

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
