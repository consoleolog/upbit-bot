"""알림. 매매 중단·체결 등 사람이 알아야 할 일을 디스코드 웹훅으로 보낸다.

알림은 매매를 막지 않는다. notify는 대기열에 넣기만 하고, 보내기는 백그라운드 서비스(run)가 한다.
보내다 실패해도(시간 초과, 거부, 연결 끊김) 로그만 남기고 다음 알림으로 넘어간다.
웹훅 URL은 비밀값이다. 로그·repr·예외 메시지에 남기지 않는다.
"""

import asyncio
import logging
from typing import Protocol
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

# 웹훅 요청 하나의 시간 제한(초)
SEND_TIMEOUT = 5.0
# 보내지 못하고 쌓일 수 있는 알림 수. 넘치면 새 알림을 버리고 로그를 남긴다 (메모리가 끝없이 늘지 않게)
QUEUE_SIZE = 100
# 디스코드 메시지 최대 길이
MAX_LENGTH = 2000
DISCORD_HOSTS = ("discord.com", "discordapp.com")


class Notifier(Protocol):
    def notify(self, text: str) -> None:
        """알림을 보낸다. 기다리지 않고 바로 돌아온다. 실패해도 예외를 내지 않는다."""
        ...


class NullNotifier:
    """알림을 쓰지 않을 때. 아무것도 하지 않는다."""

    def notify(self, text: str) -> None:
        pass


def check_discord_webhook_url(url: str) -> None:
    """디스코드 웹훅 URL 형식이 아니면 ValueError. 메시지에 URL을 넣지 않는다 (URL 자체가 비밀값)."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in DISCORD_HOSTS or not parts.path.startswith("/api/webhooks/"):
        raise ValueError("DISCORD_WEBHOOK_URL must be a https://discord.com/api/webhooks/... URL")


class DiscordNotifier:
    """디스코드 웹훅 알림. run()을 애플리케이션의 서비스로 실행해야 대기열의 알림이 나간다."""

    def __init__(
        self,
        url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = SEND_TIMEOUT,
        queue_size: int = QUEUE_SIZE,
    ) -> None:
        check_discord_webhook_url(url)
        self._url = url
        self._transport = transport
        self._timeout = timeout
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=queue_size)

    def __repr__(self) -> str:
        return "DiscordNotifier(url=<hidden>)"

    def notify(self, text: str) -> None:
        try:
            self._queue.put_nowait(text[:MAX_LENGTH])
        except asyncio.QueueFull:
            logger.warning("notification dropped: queue full (%d pending)", self._queue.qsize())

    async def run(self) -> None:
        """취소될 때까지 대기열의 알림을 하나씩 보낸다."""
        async with httpx.AsyncClient(transport=self._transport) as client:
            while True:
                text = await self._queue.get()
                await self._post(client, text)

    async def send_now(self, text: str) -> None:
        """대기열 없이 바로 보내고 끝날 때까지 기다린다. 애플리케이션이 시작되지 않았을 때(시작 실패 등)에 쓴다."""
        async with httpx.AsyncClient(transport=self._transport) as client:
            await self._post(client, text[:MAX_LENGTH])

    async def _post(self, client: httpx.AsyncClient, text: str) -> None:
        try:
            async with asyncio.timeout(self._timeout):
                # 멘션(@everyone 등)이 섞여도 실제로 알림을 울리지 않게 한다
                response = await client.post(self._url, json={"content": text, "allowed_mentions": {"parse": []}})
        # 알림 실패로 매매를 멈추지 않는다. httpx 오류 메시지에는 URL이 들어갈 수 있어 오류 종류만 남긴다
        except (TimeoutError, httpx.HTTPError) as error:
            logger.warning("discord notification failed: %s", type(error).__name__)
            return
        if not response.is_success:
            logger.warning("discord notification rejected: HTTP %d", response.status_code)
