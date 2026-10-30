import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from websockets.exceptions import ConnectionClosedError, InvalidStatus

from src.exchange.ticker import Ticker
from src.exchange.ticker_feed import FIRST_RETRY_DELAY, MAX_RETRY_DELAY, URL, TickerFeed

HANG = object()  # 아무 메시지도 오지 않음 (수신 시간 초과)


class StopFeedError(Exception):
    """시험에서 수신 루프를 끝내기 위한 예외. 네트워크 오류가 아니므로 TickerFeed가 그대로 올린다."""


def _message(market: str = "KRW-BTC", price: str = "104438000.00000000", timestamp: int = 1793357672361) -> bytes:
    return (
        f'{{"type":"ticker","code":"{market}","trade_price":{price},"trade_timestamp":{timestamp},'
        f'"stream_type":"REALTIME"}}'
    ).encode()


class FakeConnection:
    def __init__(self, events: list[object]) -> None:
        self.events = events
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        if not self.events:
            raise StopFeedError
        event = self.events.pop(0)
        if event is HANG:
            await asyncio.Event().wait()
        if isinstance(event, BaseException):
            raise event
        assert isinstance(event, str | bytes)
        return event


class FakeServer:
    """연결마다 정해 둔 이벤트(메시지·예외·무응답)를 차례로 돌려준다. 연결 자체가 실패하게 할 수도 있다."""

    def __init__(self, *sessions: list[object] | BaseException) -> None:
        self.sessions = list(sessions)
        self.connections: list[FakeConnection] = []
        self.urls: list[str] = []

    @asynccontextmanager
    async def connect(self, url: str) -> AsyncIterator[FakeConnection]:
        self.urls.append(url)
        if not self.sessions:
            raise StopFeedError
        session = self.sessions.pop(0)
        if isinstance(session, BaseException):
            raise session
        connection = FakeConnection(session)
        self.connections.append(connection)
        yield connection


class Recorder:
    def __init__(self) -> None:
        self.tickers: list[Ticker] = []
        self.sleeps: list[float] = []

    def on_ticker(self, ticker: Ticker) -> None:
        self.tickers.append(ticker)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


async def _run(server: FakeServer, markets: tuple[str, ...] = ("KRW-BTC", "KRW-XRP")) -> Recorder:
    recorder = Recorder()
    feed = TickerFeed(
        markets=markets,
        on_ticker=recorder.on_ticker,
        connect=server.connect,
        sleep=recorder.sleep,
        receive_timeout=0.01,
    )
    with pytest.raises(StopFeedError):
        await feed.run()
    return recorder


async def test_subscribes_and_delivers_tickers() -> None:
    server = FakeServer([_message("KRW-BTC"), _message("KRW-XRP", "1692.00000000")])

    recorder = await _run(server)

    assert server.urls == [URL]
    subscription = json.loads(server.connections[0].sent[0])
    assert set(subscription[0]) == {"ticket"}
    assert subscription[1] == {"type": "ticker", "codes": ["KRW-BTC", "KRW-XRP"]}
    assert recorder.tickers == [
        Ticker(
            market="KRW-BTC",
            price=Decimal("104438000"),
            traded_at=datetime(2026, 10, 30, 10, 54, 32, 361000, tzinfo=UTC),
        ),
        Ticker(
            market="KRW-XRP", price=Decimal("1692"), traded_at=datetime(2026, 10, 30, 10, 54, 32, 361000, tzinfo=UTC)
        ),
    ]


async def test_price_is_read_as_decimal_without_float_rounding() -> None:
    server = FakeServer([_message(price="0.12345678901234567890")])

    recorder = await _run(server)

    assert recorder.tickers[0].price == Decimal("0.12345678901234567890")


async def test_invalid_messages_are_skipped(caplog: pytest.LogCaptureFixture) -> None:
    server = FakeServer(
        [
            b"{not json",
            b"\xff\xfe",
            b'{"type":"trade","code":"KRW-BTC"}',
            _message(price="0"),
            _message("KRW-ETH"),
            _message("KRW-BTC"),
        ]
    )

    recorder = await _run(server)

    # 잘못된 메시지를 건너뛰고 수신을 계속한다
    assert [t.market for t in recorder.tickers] == ["KRW-BTC"]
    assert caplog.text.count("ignored invalid ticker message") == 4
    assert "ignored ticker for unsubscribed market: KRW-ETH" in caplog.text


async def test_silence_triggers_reconnect(caplog: pytest.LogCaptureFixture) -> None:
    server = FakeServer([_message(), HANG], [_message("KRW-XRP", "1692")])

    recorder = await _run(server)

    assert "ticker feed stale" in caplog.text
    assert len(server.connections) == 2
    # 다시 연결하면 구독도 다시 보낸다
    assert server.connections[1].sent == server.connections[0].sent
    assert [t.market for t in recorder.tickers] == ["KRW-BTC", "KRW-XRP"]


async def test_reconnect_delay_doubles_up_to_max_and_resets_after_message(caplog: pytest.LogCaptureFixture) -> None:
    closed = ConnectionClosedError(None, None)
    server = FakeServer(
        OSError("network is unreachable"),
        OSError("network is unreachable"),
        [closed],
        *[OSError("down")] * 6,
        [_message(), closed],
        [_message()],
    )

    recorder = await _run(server)

    # 1, 2, 4, ... 로 늘다가 60초를 넘지 않고, 시세를 받은 뒤에는 처음 값으로 돌아간다
    assert recorder.sleeps == [1, 2, 4, 8, 16, 32, MAX_RETRY_DELAY, MAX_RETRY_DELAY, MAX_RETRY_DELAY, FIRST_RETRY_DELAY]
    assert "ticker feed disconnected (OSError: network is unreachable); retrying in 1.0s" in caplog.text
    assert len(recorder.tickers) == 2


async def test_connect_timeout_is_reported_as_disconnect_not_stale(caplog: pytest.LogCaptureFixture) -> None:
    server = FakeServer(TimeoutError("timed out during opening handshake"), [_message()])

    recorder = await _run(server)

    # 접속 자체가 안 된 것이지, 연결된 뒤 시세가 끊긴 것이 아니다
    assert "ticker feed disconnected (TimeoutError: timed out during opening handshake)" in caplog.text
    assert "ticker feed stale" not in caplog.text
    assert len(recorder.tickers) == 1


async def test_rejected_handshake_is_retried() -> None:
    response = type("Response", (), {"status_code": 429})()
    server = FakeServer(InvalidStatus(response), [_message()])  # type: ignore[arg-type]

    recorder = await _run(server)

    assert recorder.sleeps == [FIRST_RETRY_DELAY]
    assert len(recorder.tickers) == 1


async def test_callback_error_is_not_swallowed() -> None:
    def broken(ticker: Ticker) -> None:
        raise RuntimeError("bug in callback")

    feed = TickerFeed(markets=["KRW-BTC"], on_ticker=broken, connect=FakeServer([_message()]).connect)

    # 네트워크 오류가 아닌 예외는 다시 연결하지 않고 그대로 올린다 (애플리케이션이 멈춘다)
    with pytest.raises(RuntimeError, match="bug in callback"):
        await feed.run()


async def test_cancel_stops_feed() -> None:
    server = FakeServer([HANG])
    feed = TickerFeed(markets=["KRW-BTC"], on_ticker=lambda ticker: None, connect=server.connect)
    task = asyncio.create_task(feed.run())
    await asyncio.sleep(0.01)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


def test_needs_markets() -> None:
    with pytest.raises(ValueError, match="at least one market"):
        TickerFeed(markets=[], on_ticker=lambda ticker: None)
