from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest
import websockets.exceptions

from src.connections.market_data import MarketDataFeed


def make_feed(**kwargs) -> MarketDataFeed:
    defaults = {"codes": ["krw-btc", "krw-eth"], "types": ["ticker"]}
    return MarketDataFeed(**{**defaults, **kwargs})


@pytest.fixture
def mock_ws():
    ws = AsyncMock()
    ws.open = True
    return ws


@pytest.fixture
def mock_ws_connect(mock_ws):
    """websockets.connect() 를 모킹하는 픽스처."""
    with patch("src.connections.market_data.websockets.connect") as mock:
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=mock_ws)
        cm.__aexit__ = AsyncMock(return_value=False)
        mock.return_value = cm
        yield mock, mock_ws


# ---------------------------------------------------------------------------
# __init__ — 초기화
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_codes_are_uppercased():
    """소문자 마켓 코드가 대문자로 변환된다."""
    feed = make_feed(codes=["krw-btc", "krw-eth"])
    assert feed.codes == ["KRW-BTC", "KRW-ETH"]


@pytest.mark.unit
def test_codes_already_uppercase_unchanged():
    """이미 대문자인 코드는 그대로 유지된다."""
    feed = make_feed(codes=["KRW-BTC"])
    assert feed.codes == ["KRW-BTC"]


@pytest.mark.unit
def test_types_stored():
    """types 파라미터가 그대로 저장된다."""
    feed = make_feed(types=["candle.1m", "ticker"])
    assert feed.types == ["candle.1m", "ticker"]


@pytest.mark.unit
def test_callbacks_default_to_none():
    """콜백이 전달되지 않으면 모두 None으로 초기화된다."""
    feed = make_feed()
    assert feed.on_ticker is None
    assert feed.on_candle is None
    assert feed.on_candle_close is None


@pytest.mark.unit
def test_callbacks_stored():
    """콜백이 전달되면 올바르게 저장된다."""
    ticker_cb = MagicMock()
    candle_cb = MagicMock()
    close_cb = MagicMock()
    feed = make_feed(on_ticker=ticker_cb, on_candle=candle_cb, on_candle_close=close_cb)
    assert feed.on_ticker is ticker_cb
    assert feed.on_candle is candle_cb
    assert feed.on_candle_close is close_cb


@pytest.mark.unit
def test_reconnect_settings_stored():
    """재연결 설정이 올바르게 저장된다."""
    feed = make_feed(reconnect_delay=3, max_reconnect_attempts=5)
    assert feed.reconnect_delay == 3
    assert feed.max_reconnect_attempts == 5


@pytest.mark.unit
def test_reconnect_settings_defaults():
    """재연결 설정의 기본값이 올바르게 설정된다."""
    feed = make_feed()
    assert feed.reconnect_delay == 5
    assert feed.max_reconnect_attempts == 10


@pytest.mark.unit
def test_initial_state():
    """초기 상태 값이 올바르게 설정된다."""
    feed = make_feed()
    assert feed._ws is None
    assert feed._running is False
    assert feed._reconnect_count == 0


# ---------------------------------------------------------------------------
# is_connected
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_is_connected_false_when_ws_is_none():
    """_ws가 None이면 is_connected는 False를 반환한다."""
    feed = make_feed()
    assert feed.is_connected is False


@pytest.mark.unit
def test_is_connected_false_when_ws_not_open():
    """_ws가 존재하지만 open이 False이면 is_connected는 False를 반환한다."""
    feed = make_feed()
    feed._ws = MagicMock()
    feed._ws.open = False
    assert feed.is_connected is False


@pytest.mark.unit
def test_is_connected_true_when_ws_open():
    """_ws가 존재하고 open이 True이면 is_connected는 True를 반환한다."""
    feed = make_feed()
    feed._ws = MagicMock()
    feed._ws.open = True
    assert feed.is_connected is True


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_disconnect_sets_running_false():
    """disconnect() 호출 후 _running이 False로 설정된다."""
    feed = make_feed()
    feed._running = True
    await feed.disconnect()
    assert feed._running is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_disconnect_closes_ws_when_connected():
    """_ws가 있으면 disconnect() 시 close()가 호출된다."""
    feed = make_feed()
    mock_ws = AsyncMock()
    feed._ws = mock_ws
    await feed.disconnect()
    mock_ws.close.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_disconnect_sets_ws_to_none():
    """disconnect() 후 _ws가 None으로 설정된다."""
    feed = make_feed()
    feed._ws = AsyncMock()
    await feed.disconnect()
    assert feed._ws is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_disconnect_when_not_connected():
    """_ws가 None인 상태에서 disconnect()를 호출해도 오류가 발생하지 않는다."""
    feed = make_feed()
    await feed.disconnect()


# ---------------------------------------------------------------------------
# _build_subscription_message
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_subscription_first_element_is_ticket():
    """구독 메시지의 첫 번째 요소가 ticket을 포함한다."""
    feed = make_feed(types=["ticker"])
    payload = orjson.loads(feed._build_subscription_message())
    assert "ticket" in payload[0]


@pytest.mark.unit
def test_build_subscription_ticket_is_uuid_string():
    """ticket 값이 UUID 형식의 문자열이다."""
    import uuid

    feed = make_feed(types=["ticker"])
    payload = orjson.loads(feed._build_subscription_message())
    ticket = payload[0]["ticket"]
    uuid.UUID(ticket)  # 유효하지 않으면 ValueError 발생


@pytest.mark.unit
def test_build_subscription_type_entries():
    """types 각각에 대한 구독 항목이 생성된다."""
    feed = make_feed(types=["ticker", "candle.1m"])
    payload = orjson.loads(feed._build_subscription_message())
    type_entries = payload[1:]
    assert len(type_entries) == 2
    assert type_entries[0] == {"type": "ticker", "codes": ["KRW-BTC", "KRW-ETH"]}
    assert type_entries[1] == {"type": "candle.1m", "codes": ["KRW-BTC", "KRW-ETH"]}


@pytest.mark.unit
def test_build_subscription_returns_bytes():
    """_build_subscription_message()는 bytes를 반환한다."""
    feed = make_feed()
    assert isinstance(feed._build_subscription_message(), bytes)


@pytest.mark.unit
def test_build_subscription_ticket_unique_per_call():
    """호출할 때마다 다른 ticket 값이 생성된다."""
    feed = make_feed()
    t1 = orjson.loads(feed._build_subscription_message())[0]["ticket"]
    t2 = orjson.loads(feed._build_subscription_message())[0]["ticket"]
    assert t1 != t2


# ---------------------------------------------------------------------------
# _handle_message
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_message_routes_to_ticker():
    """type이 'ticker'인 메시지는 _handle_ticker로 라우팅된다."""
    feed = make_feed()
    feed._handle_ticker = AsyncMock()
    msg = orjson.dumps({"type": "ticker", "code": "KRW-BTC"})
    await feed._handle_message(msg)
    feed._handle_ticker.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_message_routes_to_candle():
    """type에 'candle'이 포함된 메시지는 _handle_candle로 라우팅된다."""
    feed = make_feed()
    feed._handle_candle = AsyncMock()
    msg = orjson.dumps({"type": "candle.1m", "code": "KRW-BTC"})
    await feed._handle_message(msg)
    feed._handle_candle.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_message_non_dict_skipped():
    """dict가 아닌 JSON 데이터는 콜백을 호출하지 않는다."""
    feed = make_feed()
    feed._handle_ticker = AsyncMock()
    feed._handle_candle = AsyncMock()
    await feed._handle_message(orjson.dumps([1, 2, 3]))
    feed._handle_ticker.assert_not_called()
    feed._handle_candle.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_message_invalid_json_does_not_raise():
    """잘못된 JSON 메시지가 들어와도 예외가 전파되지 않는다."""
    feed = make_feed()
    await feed._handle_message(b"not-json")  # should not raise


# ---------------------------------------------------------------------------
# _handle_ticker
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_ticker_calls_sync_callback():
    """동기 on_ticker 콜백이 올바르게 호출된다."""
    callback = MagicMock()
    feed = make_feed(on_ticker=callback)

    with patch("src.connections.market_data.Ticker") as mock_ticker_cls:
        mock_ticker = MagicMock()
        mock_ticker_cls.from_dict.return_value = mock_ticker
        await feed._handle_ticker({"type": "ticker"})

    callback.assert_called_once_with(mock_ticker)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_ticker_calls_async_callback():
    """비동기 on_ticker 콜백이 올바르게 awaited된다."""
    callback = AsyncMock()
    feed = make_feed(on_ticker=callback)

    with patch("src.connections.market_data.Ticker") as mock_ticker_cls:
        mock_ticker_cls.from_dict.return_value = MagicMock()
        await feed._handle_ticker({"type": "ticker"})

    callback.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_ticker_no_callback_does_not_raise():
    """on_ticker가 None이어도 예외가 발생하지 않는다."""
    feed = make_feed()
    with patch("src.connections.market_data.Ticker") as mock_ticker_cls:
        mock_ticker_cls.from_dict.return_value = MagicMock()
        await feed._handle_ticker({"type": "ticker"})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_ticker_callback_exception_does_not_propagate():
    """on_ticker 콜백에서 예외가 발생해도 전파되지 않는다."""
    callback = MagicMock(side_effect=RuntimeError("콜백 오류"))
    feed = make_feed(on_ticker=callback)
    with patch("src.connections.market_data.Ticker") as mock_ticker_cls:
        mock_ticker_cls.from_dict.return_value = MagicMock()
        await feed._handle_ticker({"type": "ticker"})  # should not raise


# ---------------------------------------------------------------------------
# _handle_candle
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_candle_calls_sync_callback():
    """동기 on_candle 콜백이 올바르게 호출된다."""
    callback = MagicMock()
    feed = make_feed(on_candle=callback)

    with patch("src.connections.market_data.Candle") as mock_candle_cls:
        mock_candle = MagicMock()
        mock_candle_cls.from_dict.return_value = mock_candle
        await feed._handle_candle({"type": "candle.1m"})

    callback.assert_called_once_with(mock_candle)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_candle_calls_async_callback():
    """비동기 on_candle 콜백이 올바르게 awaited된다."""
    callback = AsyncMock()
    feed = make_feed(on_candle=callback)

    with patch("src.connections.market_data.Candle") as mock_candle_cls:
        mock_candle_cls.from_dict.return_value = MagicMock()
        await feed._handle_candle({"type": "candle.1m"})

    callback.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_candle_no_callback_does_not_raise():
    """on_candle이 None이어도 예외가 발생하지 않는다."""
    feed = make_feed()
    with patch("src.connections.market_data.Candle") as mock_candle_cls:
        mock_candle_cls.from_dict.return_value = MagicMock()
        await feed._handle_candle({"type": "candle.1m"})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_candle_callback_exception_does_not_propagate():
    """on_candle 콜백에서 예외가 발생해도 전파되지 않는다."""
    callback = MagicMock(side_effect=RuntimeError("콜백 오류"))
    feed = make_feed(on_candle=callback)
    with patch("src.connections.market_data.Candle") as mock_candle_cls:
        mock_candle_cls.from_dict.return_value = MagicMock()
        await feed._handle_candle({"type": "candle.1m"})  # should not raise


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_candle_calls_on_candle_close_when_closed():
    """is_closed가 True이면 on_candle_close 콜백이 호출된다."""
    close_cb = MagicMock()
    feed = make_feed(on_candle_close=close_cb)

    with patch("src.connections.market_data.Candle") as mock_candle_cls:
        mock_candle = MagicMock()
        mock_candle.is_closed = True
        mock_candle_cls.from_dict.return_value = mock_candle
        await feed._handle_candle({"type": "candle.1m"})

    close_cb.assert_called_once_with(mock_candle)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_candle_does_not_call_on_candle_close_when_not_closed():
    """is_closed가 False이면 on_candle_close 콜백이 호출되지 않는다."""
    close_cb = MagicMock()
    feed = make_feed(on_candle_close=close_cb)

    with patch("src.connections.market_data.Candle") as mock_candle_cls:
        mock_candle = MagicMock()
        mock_candle.is_closed = False
        mock_candle_cls.from_dict.return_value = mock_candle
        await feed._handle_candle({"type": "candle.1m"})

    close_cb.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_candle_calls_async_on_candle_close():
    """비동기 on_candle_close 콜백이 올바르게 awaited된다."""
    close_cb = AsyncMock()
    feed = make_feed(on_candle_close=close_cb)

    with patch("src.connections.market_data.Candle") as mock_candle_cls:
        mock_candle = MagicMock()
        mock_candle.is_closed = True
        mock_candle_cls.from_dict.return_value = mock_candle
        await feed._handle_candle({"type": "candle.1m"})

    close_cb.assert_awaited_once_with(mock_candle)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_candle_on_candle_close_exception_does_not_propagate():
    """on_candle_close 콜백에서 예외가 발생해도 전파되지 않는다."""
    close_cb = MagicMock(side_effect=RuntimeError("마감 콜백 오류"))
    feed = make_feed(on_candle_close=close_cb)

    with patch("src.connections.market_data.Candle") as mock_candle_cls:
        mock_candle = MagicMock()
        mock_candle.is_closed = True
        mock_candle_cls.from_dict.return_value = mock_candle
        await feed._handle_candle({"type": "candle.1m"})  # should not raise


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_connect_sends_subscription_message(mock_ws_connect):
    """connect() 시 구독 메시지가 WebSocket으로 전송된다."""
    _, mock_ws = mock_ws_connect
    feed = make_feed(types=["ticker"])

    # recv가 ConnectionClosed를 발생시켜 내부 루프를 종료하고, 외부 루프도 중단
    def stop_and_close():
        feed._running = False
        raise websockets.exceptions.ConnectionClosed(None, None)

    mock_ws.recv.side_effect = stop_and_close

    await feed.connect()

    mock_ws.send.assert_awaited_once()
    sent = orjson.loads(mock_ws.send.call_args[0][0])
    assert sent[0].get("ticket") is not None
    assert sent[1]["type"] == "ticker"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_connect_resets_reconnect_count_on_success(mock_ws_connect):
    """연결 성공 시 _reconnect_count가 0으로 리셋된다."""
    _, mock_ws = mock_ws_connect
    feed = make_feed()
    feed._reconnect_count = 3

    def stop_and_close():
        feed._running = False
        raise websockets.exceptions.ConnectionClosed(None, None)

    mock_ws.recv.side_effect = stop_and_close

    await feed.connect()

    assert feed._reconnect_count == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_connect_increments_reconnect_count_on_error():
    """연결 오류 시 _reconnect_count가 증가한다."""
    feed = make_feed(reconnect_delay=0, max_reconnect_attempts=1)

    with (
        patch("src.connections.market_data.websockets.connect", side_effect=OSError("연결 실패")),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        await feed.connect()

    assert feed._reconnect_count > 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_connect_stops_after_max_reconnect_attempts():
    """max_reconnect_attempts 초과 시 connect() 루프가 종료된다."""
    feed = make_feed(reconnect_delay=0, max_reconnect_attempts=2)

    with (
        patch("src.connections.market_data.websockets.connect", side_effect=OSError("연결 실패")),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        await feed.connect()

    assert feed._reconnect_count > feed.max_reconnect_attempts


# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ws_url_quotation():
    """WS_URL_QUOTATION이 업비트 공개 시세 엔드포인트를 가리킨다."""
    assert MarketDataFeed.WS_URL_QUOTATION == "wss://api.upbit.com/websocket/v1"


@pytest.mark.unit
def test_ws_url_exchange_is_private_endpoint():
    """WS_URL_EXCHANGE가 /private 경로를 포함한다."""
    assert MarketDataFeed.WS_URL_EXCHANGE == "wss://api.upbit.com/websocket/v1/private"
