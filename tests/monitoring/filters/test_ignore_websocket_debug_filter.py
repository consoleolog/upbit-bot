import logging

import pytest

from src.monitoring.filters.ignore_websocket_debug_filter import IgnoreWebsocketDebugFilter


def make_record(name: str, level: int, message: str = "test message") -> logging.LogRecord:
    """지정한 로거 이름과 레벨로 LogRecord 를 생성한다."""
    return logging.LogRecord(name=name, level=level, pathname="", lineno=0, msg=message, args=(), exc_info=None)


@pytest.fixture
def filter_() -> IgnoreWebsocketDebugFilter:
    return IgnoreWebsocketDebugFilter()


# ---------------------------------------------------------------------------
# websockets.client — DEBUG 필터링
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_websockets_client_debug_is_filtered(filter_):
    """websockets.client 로거의 DEBUG 레코드는 필터링한다."""
    record = make_record("websockets.client", logging.DEBUG)
    assert filter_.filter(record) is False


@pytest.mark.unit
def test_websockets_client_sublogger_debug_is_filtered(filter_):
    """websockets.client.* 하위 로거의 DEBUG 레코드도 필터링한다."""
    record = make_record("websockets.client.protocol", logging.DEBUG)
    assert filter_.filter(record) is False


# ---------------------------------------------------------------------------
# websockets.client — DEBUG 이외 레벨 허용
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_websockets_client_info_is_allowed(filter_):
    """websockets.client 로거의 INFO 레코드는 허용한다."""
    record = make_record("websockets.client", logging.INFO)
    assert filter_.filter(record) is True


@pytest.mark.unit
def test_websockets_client_warning_is_allowed(filter_):
    """websockets.client 로거의 WARNING 레코드는 허용한다."""
    record = make_record("websockets.client", logging.WARNING)
    assert filter_.filter(record) is True


@pytest.mark.unit
def test_websockets_client_error_is_allowed(filter_):
    """websockets.client 로거의 ERROR 레코드는 허용한다."""
    record = make_record("websockets.client", logging.ERROR)
    assert filter_.filter(record) is True


@pytest.mark.unit
def test_websockets_client_critical_is_allowed(filter_):
    """websockets.client 로거의 CRITICAL 레코드는 허용한다."""
    record = make_record("websockets.client", logging.CRITICAL)
    assert filter_.filter(record) is True


# ---------------------------------------------------------------------------
# 다른 로거 — 레벨 무관 허용
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_websockets_server_debug_is_allowed(filter_):
    """websockets.server 로거는 websockets.client 가 아니므로 DEBUG 도 허용한다."""
    record = make_record("websockets.server", logging.DEBUG)
    assert filter_.filter(record) is True


@pytest.mark.unit
def test_websockets_root_debug_is_allowed(filter_):
    """websockets 루트 로거의 DEBUG 는 허용한다."""
    record = make_record("websockets", logging.DEBUG)
    assert filter_.filter(record) is True


@pytest.mark.unit
def test_myapp_debug_is_allowed(filter_):
    """임의의 애플리케이션 로거의 DEBUG 는 허용한다."""
    record = make_record("myapp.connection", logging.DEBUG)
    assert filter_.filter(record) is True


@pytest.mark.unit
def test_root_logger_debug_is_allowed(filter_):
    """root 로거의 DEBUG 는 허용한다."""
    record = make_record("root", logging.DEBUG)
    assert filter_.filter(record) is True
