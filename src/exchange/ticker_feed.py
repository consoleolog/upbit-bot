"""업비트 웹소켓 현재가(ticker) 수신. 실시간 손절·익절 판단에 쓴다 (매매 로직 5.1)."""

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from decimal import Decimal
from typing import Protocol

import websockets
from websockets.exceptions import WebSocketException

from src.exchange.ticker import Ticker, parse_ticker
from src.exchange.upbit_client import UpbitResponseError

logger = logging.getLogger(__name__)

URL = "wss://api.upbit.com/websocket/v1"
# 시세는 구독한 마켓 중 어디서든 체결이 있을 때마다 온다(KRW-BTC·ETH·XRP 합쳐 평소 초당 1~2건).
# 이만큼 아무것도 오지 않으면 연결에 문제가 있다고 보고 다시 연결한다
RECEIVE_TIMEOUT = 30.0
# 다시 연결하기 전 대기 시간(초). 실패할 때마다 두 배로 늘리고 최대값을 넘지 않는다. 시세를 받으면 처음 값으로 돌아간다
FIRST_RETRY_DELAY = 1.0
MAX_RETRY_DELAY = 60.0


class _StaleError(Exception):
    """receive_timeout 동안 시세를 하나도 받지 못함."""


class Connection(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...


Connect = Callable[[str], AbstractAsyncContextManager[Connection]]


def _connect(url: str) -> AbstractAsyncContextManager[Connection]:
    # 연결 시간 제한 10초. 연결 유지 ping은 websockets 기본값(20초 간격)을 쓴다
    return websockets.connect(url, open_timeout=10)


class TickerFeed:
    """마켓들의 현재가를 받아 on_ticker로 넘긴다. 끊기면 계속 다시 연결한다.

    네트워크 오류와 시세 끊김은 다시 연결로 처리한다. 그 밖의 예외(on_ticker의 오류 등)는 그대로 올린다.
    """

    def __init__(
        self,
        *,
        markets: Sequence[str],
        on_ticker: Callable[[Ticker], None],
        connect: Connect = _connect,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        receive_timeout: float = RECEIVE_TIMEOUT,
    ) -> None:
        if not markets:
            raise ValueError("ticker feed needs at least one market")
        self._markets = frozenset(markets)
        self._subscription = json.dumps(
            [{"ticket": str(uuid.uuid4())}, {"type": "ticker", "codes": sorted(self._markets)}]
        )
        self._on_ticker = on_ticker
        self._connect = connect
        self._sleep = sleep
        self._receive_timeout = receive_timeout

    async def run(self) -> None:
        """취소될 때까지 시세를 받는다. 애플리케이션의 서비스로 실행한다."""
        delay = FIRST_RETRY_DELAY
        while True:
            try:
                async with self._connect(URL) as connection:
                    await connection.send(self._subscription)
                    logger.info("ticker feed connected (markets=%s)", ",".join(sorted(self._markets)))
                    while True:
                        self._handle(await self._receive(connection))
                        delay = FIRST_RETRY_DELAY
            except _StaleError:
                logger.warning("ticker feed stale: no message for %ss; reconnecting", self._receive_timeout)
            # 연결 시간 초과(TimeoutError)도 OSError의 하위 클래스라 여기서 연결 실패로 처리한다
            except (OSError, WebSocketException) as error:
                logger.warning("ticker feed disconnected (%s: %s); retrying in %ss", type(error).__name__, error, delay)
            await self._sleep(delay)
            delay = min(delay * 2, MAX_RETRY_DELAY)

    async def _receive(self, connection: Connection) -> str | bytes:
        try:
            async with asyncio.timeout(self._receive_timeout):
                return await connection.recv()
        except TimeoutError as error:
            raise _StaleError from error

    def _handle(self, message: str | bytes) -> None:
        # 잘못된 메시지 하나로 수신을 멈추지 않는다. 건너뛰고 기록한다
        try:
            ticker = parse_ticker(json.loads(message, parse_float=Decimal))
        except (json.JSONDecodeError, UnicodeDecodeError, UpbitResponseError) as error:
            logger.warning("ignored invalid ticker message: %s", error)
            return
        if ticker.market not in self._markets:
            logger.warning("ignored ticker for unsubscribed market: %s", ticker.market)
            return
        self._on_ticker(ticker)
