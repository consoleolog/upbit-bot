import logging

import pytest

from src.monitoring.filters.ignore_port_scanners_filter import IgnorePortScannersFilter


def make_record(name: str, message: str, level: int = logging.ERROR) -> logging.LogRecord:
    """지정한 로거 이름과 메시지로 LogRecord 를 생성한다."""
    record = logging.LogRecord(name=name, level=level, pathname="", lineno=0, msg=message, args=(), exc_info=None)
    return record


@pytest.fixture
def filter_() -> IgnorePortScannersFilter:
    return IgnorePortScannersFilter()


# ---------------------------------------------------------------------------
# 클래스 속성
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ignored_patterns_contains_bad_status_line(filter_):
    """IGNORED_PATTERNS 에 BadStatusLine 이 포함된다."""
    assert "BadStatusLine" in filter_.IGNORED_PATTERNS


@pytest.mark.unit
def test_ignored_patterns_contains_invalid_method(filter_):
    """IGNORED_PATTERNS 에 Invalid method encountered 가 포함된다."""
    assert "Invalid method encountered" in filter_.IGNORED_PATTERNS


@pytest.mark.unit
def test_ignored_patterns_contains_error_handling_request(filter_):
    """IGNORED_PATTERNS 에 Error handling request 가 포함된다."""
    assert "Error handling request" in filter_.IGNORED_PATTERNS


@pytest.mark.unit
def test_scanner_bytes_contains_ssl(filter_):
    """SCANNER_BYTES 에 SSL/TLS 핸드셰이크 바이트 패턴이 포함된다."""
    assert "\\x16\\x03\\x01" in filter_.SCANNER_BYTES


@pytest.mark.unit
def test_scanner_bytes_contains_socks4(filter_):
    """SCANNER_BYTES 에 SOCKS4 바이트 패턴이 포함된다."""
    assert "\\x04\\x01" in filter_.SCANNER_BYTES


@pytest.mark.unit
def test_scanner_bytes_contains_socks5(filter_):
    """SCANNER_BYTES 에 SOCKS5 바이트 패턴이 포함된다."""
    assert "\\x05\\x01" in filter_.SCANNER_BYTES


@pytest.mark.unit
def test_scanner_bytes_contains_ssh(filter_):
    """SCANNER_BYTES 에 SSH 스캐너 패턴이 포함된다."""
    assert "SSH-2.0" in filter_.SCANNER_BYTES


@pytest.mark.unit
def test_scanner_bytes_contains_get_http(filter_):
    """SCANNER_BYTES 에 GET / HTTP 패턴이 포함된다."""
    assert "GET / HTTP" in filter_.SCANNER_BYTES


# ---------------------------------------------------------------------------
# aiohttp.server 이외 로거 — 항상 허용
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_non_aiohttp_logger_is_allowed(filter_):
    """aiohttp.server 가 아닌 로거의 레코드는 항상 허용한다."""
    record = make_record("myapp.server", "BadStatusLine \\x16\\x03\\x01")
    assert filter_.filter(record) is True


@pytest.mark.unit
def test_root_logger_is_allowed(filter_):
    """root 로거의 레코드는 항상 허용한다."""
    record = make_record("root", "BadStatusLine \\x16\\x03\\x01")
    assert filter_.filter(record) is True


@pytest.mark.unit
def test_aiohttp_access_logger_is_allowed(filter_):
    """aiohttp.access 로거는 aiohttp.server 가 아니므로 허용한다."""
    record = make_record("aiohttp.access", "BadStatusLine \\x16\\x03\\x01")
    assert filter_.filter(record) is True


# ---------------------------------------------------------------------------
# aiohttp.server — 일반 오류 메시지 허용
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_aiohttp_server_normal_error_is_allowed(filter_):
    """aiohttp.server 로거라도 스캐너 패턴이 없는 일반 오류는 허용한다."""
    record = make_record("aiohttp.server", "Connection reset by peer")
    assert filter_.filter(record) is True


@pytest.mark.unit
def test_aiohttp_server_bad_status_line_without_scanner_bytes_is_allowed(filter_):
    """BadStatusLine 패턴이 있어도 스캐너 바이트가 없으면 허용한다."""
    record = make_record("aiohttp.server", "BadStatusLine: malformed request")
    assert filter_.filter(record) is True


@pytest.mark.unit
def test_aiohttp_server_invalid_method_without_scanner_bytes_is_allowed(filter_):
    """Invalid method encountered 패턴이 있어도 스캐너 바이트/시퀀스가 없으면 허용한다."""
    record = make_record("aiohttp.server", "Invalid method encountered: PATCH")
    assert filter_.filter(record) is True


@pytest.mark.unit
def test_aiohttp_server_error_handling_without_scanner_bytes_is_allowed(filter_):
    """Error handling request 패턴이 있어도 스캐너 바이트가 없으면 허용한다."""
    record = make_record("aiohttp.server", "Error handling request from 127.0.0.1")
    assert filter_.filter(record) is True


# ---------------------------------------------------------------------------
# aiohttp.server — IGNORED_PATTERNS + SCANNER_BYTES 조합 → 필터링
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_bad_status_line_with_ssl_bytes_is_filtered(filter_):
    """BadStatusLine + SSL/TLS 핸드셰이크 바이트 조합은 필터링한다."""
    record = make_record("aiohttp.server", "BadStatusLine \\x16\\x03\\x01")
    assert filter_.filter(record) is False


@pytest.mark.unit
def test_bad_status_line_with_socks4_bytes_is_filtered(filter_):
    """BadStatusLine + SOCKS4 바이트 조합은 필터링한다."""
    record = make_record("aiohttp.server", "BadStatusLine \\x04\\x01")
    assert filter_.filter(record) is False


@pytest.mark.unit
def test_bad_status_line_with_socks5_bytes_is_filtered(filter_):
    """BadStatusLine + SOCKS5 바이트 조합은 필터링한다."""
    record = make_record("aiohttp.server", "BadStatusLine \\x05\\x01")
    assert filter_.filter(record) is False


@pytest.mark.unit
def test_bad_status_line_with_ssh_is_filtered(filter_):
    """BadStatusLine + SSH 스캐너 패턴 조합은 필터링한다."""
    record = make_record("aiohttp.server", "BadStatusLine SSH-2.0-OpenSSH_8.0")
    assert filter_.filter(record) is False


@pytest.mark.unit
def test_bad_status_line_with_get_http_is_filtered(filter_):
    """BadStatusLine + GET / HTTP 패턴 조합은 필터링한다."""
    record = make_record("aiohttp.server", "BadStatusLine GET / HTTP/1.0")
    assert filter_.filter(record) is False


@pytest.mark.unit
def test_invalid_method_with_ssl_bytes_is_filtered(filter_):
    """Invalid method encountered + SSL 바이트 조합은 필터링한다."""
    record = make_record("aiohttp.server", "Invalid method encountered \\x16\\x03\\x01")
    assert filter_.filter(record) is False


@pytest.mark.unit
def test_error_handling_with_ssh_is_filtered(filter_):
    """Error handling request + SSH 스캐너 패턴 조합은 필터링한다."""
    record = make_record("aiohttp.server", "Error handling request SSH-2.0-libssh")
    assert filter_.filter(record) is False


# ---------------------------------------------------------------------------
# aiohttp.server — Invalid method + 바이트 시퀀스 표기 → 필터링
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_invalid_method_with_single_quote_bytes_is_filtered(filter_):
    """Invalid method + b' 바이트 시퀀스 표기 조합은 필터링한다."""
    record = make_record("aiohttp.server", "Invalid method encountered b'\\x16\\x03'")
    assert filter_.filter(record) is False


@pytest.mark.unit
def test_invalid_method_with_double_quote_bytes_is_filtered(filter_):
    """Invalid method + b\" 바이트 시퀀스 표기 조합은 필터링한다."""
    record = make_record("aiohttp.server", 'Invalid method encountered b"\\x16\\x03"')
    assert filter_.filter(record) is False


@pytest.mark.unit
def test_non_invalid_method_with_byte_sequence_is_allowed(filter_):
    """Invalid method 가 없고 b' 만 있는 메시지는 허용한다."""
    record = make_record("aiohttp.server", "BadStatusLine b'some data'")
    assert filter_.filter(record) is True
