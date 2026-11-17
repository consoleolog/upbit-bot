import asyncio
import json

import httpx
import pytest

from src.app.notifier import MAX_LENGTH, DiscordNotifier, NullNotifier, check_discord_webhook_url

URL = "https://discord.com/api/webhooks/123/secret-token-abc"


class FakeDiscord:
    """받은 요청을 기록하고, 정해 둔 응답(상태 코드 또는 예외)을 차례로 돌려준다. 다 쓰면 204."""

    def __init__(self, *responses: int | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        result = self.responses.pop(0) if self.responses else 204
        if isinstance(result, Exception):
            raise result
        return httpx.Response(result)

    @property
    def contents(self) -> list[str]:
        return [json.loads(request.read())["content"] for request in self.requests]


async def _drain(notifier: DiscordNotifier, sent: FakeDiscord, count: int) -> None:
    """run()을 돌려 count개를 보낼 때까지 기다린 뒤 멈춘다."""
    task = asyncio.create_task(notifier.run())
    for _ in range(200):
        if len(sent.requests) >= count:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_queued_notifications_are_sent_in_order() -> None:
    discord = FakeDiscord()
    notifier = DiscordNotifier(URL, transport=httpx.MockTransport(discord))

    notifier.notify("첫 번째")
    notifier.notify("두 번째")
    await _drain(notifier, discord, 2)

    assert discord.contents == ["첫 번째", "두 번째"]
    request = discord.requests[0]
    assert (request.method, str(request.url)) == ("POST", URL)
    # 멘션이 섞여도 실제로 알림을 울리지 않는다
    assert json.loads(request.read())["allowed_mentions"] == {"parse": []}


async def test_notify_returns_without_waiting_for_send() -> None:
    notifier = DiscordNotifier(URL, transport=httpx.MockTransport(FakeDiscord()))

    # run()이 돌지 않아도 notify는 바로 돌아온다 (매매 판단을 막지 않음)
    notifier.notify("알림")


@pytest.mark.parametrize(
    "failure", [500, 429, httpx.ConnectError("connection refused"), httpx.ReadError("connection reset")]
)
async def test_failure_is_logged_without_url_and_next_message_is_sent(
    failure: int | Exception, caplog: pytest.LogCaptureFixture
) -> None:
    discord = FakeDiscord(failure)
    notifier = DiscordNotifier(URL, transport=httpx.MockTransport(discord))

    notifier.notify("실패할 알림")
    notifier.notify("다음 알림")
    await _drain(notifier, discord, 2)

    # 알림이 실패해도 멈추지 않고 다음 알림을 보낸다
    assert discord.contents == ["실패할 알림", "다음 알림"]
    assert "discord notification" in caplog.text
    # 웹훅 URL은 비밀값이라 로그에 남기지 않는다
    assert "secret-token-abc" not in caplog.text


async def test_slow_discord_is_cut_off_by_timeout(caplog: pytest.LogCaptureFixture) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(10)
        return httpx.Response(204)

    notifier = DiscordNotifier(URL, transport=httpx.MockTransport(slow), timeout=0.05)

    await asyncio.wait_for(notifier.send_now("느린 알림"), timeout=2)

    assert "discord notification failed: TimeoutError" in caplog.text


async def test_full_queue_drops_new_notification(caplog: pytest.LogCaptureFixture) -> None:
    discord = FakeDiscord()
    notifier = DiscordNotifier(URL, transport=httpx.MockTransport(discord), queue_size=2)

    for text in ("1", "2", "3"):
        notifier.notify(text)
    await _drain(notifier, discord, 2)

    # 보내지 못한 알림이 끝없이 쌓이지 않는다
    assert discord.contents == ["1", "2"]
    assert "notification dropped: queue full" in caplog.text


async def test_long_message_is_cut_to_discord_limit() -> None:
    discord = FakeDiscord()
    notifier = DiscordNotifier(URL, transport=httpx.MockTransport(discord))

    # 디스코드는 2,000자를 넘는 메시지를 거절하므로, 대기열로 보내든 바로 보내든 잘라서 보낸다
    notifier.notify("나" * (MAX_LENGTH + 10))
    await _drain(notifier, discord, 1)
    await notifier.send_now("가" * (MAX_LENGTH + 10))

    assert [len(text) for text in discord.contents] == [MAX_LENGTH, MAX_LENGTH]


def test_repr_hides_url() -> None:
    assert "secret-token-abc" not in repr(DiscordNotifier(URL))


@pytest.mark.parametrize(
    "url",
    [
        "http://discord.com/api/webhooks/1/x",
        "https://example.com/api/webhooks/1/x",
        "https://discord.com/channels/1/2",
        "discord.com/api/webhooks/1/x",
        "",
    ],
)
def test_invalid_webhook_url_rejected_without_echoing_it(url: str) -> None:
    with pytest.raises(ValueError, match="DISCORD_WEBHOOK_URL") as error:
        check_discord_webhook_url(url)

    assert url == "" or url not in str(error.value)


@pytest.mark.parametrize(
    "url", [URL, "https://discordapp.com/api/webhooks/1/x", "https://discord.com/api/webhooks/1/x?thread_id=2"]
)
def test_valid_webhook_url_accepted(url: str) -> None:
    check_discord_webhook_url(url)


def test_null_notifier_does_nothing() -> None:
    NullNotifier().notify("아무 일도 없음")
