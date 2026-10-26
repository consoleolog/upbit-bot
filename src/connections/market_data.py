import asyncio
import uuid
from collections.abc import Callable

import orjson
import structlog
import websockets
from websockets.exceptions import InvalidStatus

from ..models import Candle, Ticker

logger = structlog.get_logger(__name__)


class MarketDataFeed:
    """
    업비트 WebSocket 시장 데이터 수신 클래스

    지정된 마켓 코드와 스트림 타입을 구독하고, 수신된 데이터를
    콜백 함수를 통해 상위 레이어로 전달합니다.
    연결 끊김 시 지수 백오프(exponential backoff) 방식으로 자동 재연결합니다.

    Attributes:
        WS_URL_QUOTATION: 업비트 공개 시세 WebSocket 엔드포인트
        WS_URL_EXCHANGE: 업비트 인증 전용 WebSocket 엔드포인트 (자산/주문 조회)
    """

    WS_URL_QUOTATION = "wss://api.upbit.com/websocket/v1"
    WS_URL_EXCHANGE = f"{WS_URL_QUOTATION}/private"

    def __init__(
        self,
        codes: list[str],
        types: list[str],
        on_ticker: Callable | None = None,
        on_candle: Callable | None = None,
        on_candle_close: Callable | None = None,
        reconnect_delay: int = 5,
        max_reconnect_attempts: int = 10,
    ):
        """
        Args:
            codes: 구독할 마켓 코드 목록 (예: ["KRW-BTC", "KRW-ETH"])
            types: 구독할 스트림 타입 목록 (예: ["ticker"], ["candle.1m"])
            on_ticker: 티커 데이터 수신 시 호출할 콜백
            on_candle: 캔들 데이터 수신 시 호출할 콜백
            on_candle_close: 캔들 확정(봉 마감) 시 호출할 콜백
            reconnect_delay: 재연결 기본 대기 시간(초, 기본값: 5)
            max_reconnect_attempts: 최대 재연결 시도 횟수 (기본값: 10)
        """
        self.codes = [c.upper() for c in codes]
        self.types = types

        # Callbacks
        self.on_ticker = on_ticker
        self.on_candle = on_candle
        self.on_candle_close = on_candle_close

        # Reconnection settings
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_attempts = max_reconnect_attempts

        # State
        self._ws = None
        self._running = False
        self._reconnect_count = 0

    async def connect(self) -> None:
        """WebSocket에 연결하고 메시지 수신 루프를 시작합니다.

        연결 오류 발생 시 reconnect_delay 기반 지수 백오프로 재연결을 시도하며,
        max_reconnect_attempts 초과 시 루프를 종료합니다.
        """
        self._running = True
        logger.info("🚀 MarketDataFeed started", codes=len(self.codes))

        while self._running:
            try:
                async with websockets.connect(self.WS_URL_QUOTATION) as ws:
                    self._ws = ws
                    self._reconnect_count = 0

                    await ws.send(self._build_subscription_message())

                    while self._running:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=60)
                            await self._handle_message(message)
                        except asyncio.TimeoutError:
                            logger.debug("⏱️ 수신 타임아웃, ping 전송")
                            await ws.ping()
                        except websockets.exceptions.ConnectionClosed as error:
                            logger.warning("⚠️ WebSocket 연결 종료", error=str(error))
                            break

            except InvalidStatus as error:
                if error.response.status_code == 429:
                    wait = 300
                    logger.warning("⏳ Rate limit 초과 (HTTP 429)", wait=wait)
                    await asyncio.sleep(wait)
                else:
                    logger.exception("💥 WebSocket 연결 거부", status=error.response.status_code)
                    break
            except Exception as error:
                logger.exception("💥 WebSocket 오류 발생", error=str(error))

                if self._running:
                    self._reconnect_count += 1
                    if self._reconnect_count > self.max_reconnect_attempts:
                        logger.error("❌ 최대 재연결 횟수 초과, MarketDataFeed closed")
                        break

                    delay = self.reconnect_delay * min(self._reconnect_count, 5)
                    logger.info("🔄 재연결 대기 중", delay=delay, attempt=self._reconnect_count)
                    await asyncio.sleep(delay)

    def _build_subscription_message(self) -> bytes:
        """업비트 WebSocket 구독 메시지를 생성합니다.

        업비트 WebSocket API 포맷에 맞게 ticket과 타입별 구독 항목을 직렬화합니다.

        Returns:
            업비트 구독 포맷의 JSON bytes.
            예: [{"ticket": "uuid"}, {"type": "ticker", "codes": ["KRW-BTC"]}]
        """
        subscription = [{"ticket": str(uuid.uuid4())}]
        for t in self.types:
            subscription.append({"type": t, "codes": self.codes})
        return orjson.dumps(subscription)

    async def _handle_message(self, message: bytes) -> None:
        """수신된 WebSocket 메시지를 파싱하고 타입에 맞는 핸들러로 라우팅합니다.

        Args:
            message: WebSocket으로부터 수신된 raw bytes 메시지.
        """
        try:
            data = orjson.loads(message)

            if not isinstance(data, dict):
                return

            if "candle" in data.get("type"):
                await self._handle_candle(data)

            if data.get("type") == "ticker":
                await self._handle_ticker(data)

        except orjson.JSONDecodeError as error:
            logger.exception("❌ JSON 파싱 오류", error=str(error))
        except Exception as error:
            logger.exception("💥 메시지 처리 오류", error=str(error))

    async def disconnect(self) -> None:
        """WebSocket 연결을 종료하고 수신 루프를 중단합니다."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("🛑 MarketDataFeed disconnected")

    @property
    def is_connected(self) -> bool:
        """WebSocket이 현재 연결된 상태인지 반환합니다."""
        return self._ws is not None and self._ws.open

    async def _handle_ticker(self, data: dict) -> None:
        """티커 데이터를 파싱하고 on_ticker 콜백을 호출합니다.

        Args:
            data: 업비트 WebSocket에서 수신한 ticker 타입의 파싱된 딕셔너리.
        """
        ticker = Ticker.from_dict(data)

        if self.on_ticker:
            try:
                if asyncio.iscoroutinefunction(self.on_ticker):
                    await self.on_ticker(ticker)
                else:
                    self.on_ticker(ticker)
            except Exception as error:
                logger.exception("💥 on_ticker 콜백 오류", error=str(error))

    async def _handle_candle(self, data: dict) -> None:
        """캔들 데이터를 파싱하고 on_candle 콜백을 호출합니다.

        Args:
            data: 업비트 WebSocket에서 수신한 candle 타입의 파싱된 딕셔너리.
        """
        candle = Candle.from_dict(data)

        if self.on_candle:
            try:
                if asyncio.iscoroutinefunction(self.on_candle):
                    await self.on_candle(candle)
                else:
                    self.on_candle(candle)
            except Exception as error:
                logger.exception("💥 on_candle 콜백 오류", error=str(error))

        if candle.is_closed and self.on_candle_close:
            try:
                if asyncio.iscoroutinefunction(self.on_candle_close):
                    await self.on_candle_close(candle)
                else:
                    self.on_candle_close(candle)
            except Exception as error:
                logger.exception("💥 on_candle_close 콜백 오류", error=str(error))
