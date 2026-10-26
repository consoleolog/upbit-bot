import logging
from logging.handlers import RotatingFileHandler

import pytest
import structlog
from structlog.testing import LogCapture, capture_logs

from src.monitoring.logger import StructuredLogger


@pytest.fixture(autouse=True)
def reset_logging():
    """각 테스트 후 structlog 설정과 root logger 핸들러를 초기화한다."""
    yield
    structlog.reset_defaults()
    root = logging.getLogger()
    for h in root.handlers[:]:
        h.close()
        root.removeHandler(h)


@pytest.mark.unit
def test_default_config():
    """config 인자 없이 생성 시 기본값(log_level=INFO, log_dir=logs)이 적용된다."""
    logger = StructuredLogger(name="app")
    assert logger.config["log_level"] == "INFO"
    assert logger.config["log_dir"] == "logs"


@pytest.mark.unit
def test_custom_config_overrides_defaults():
    """config 인자로 전달한 키는 기본값을 덮어쓰고, 나머지 키는 기본값을 유지한다."""
    logger = StructuredLogger(name="app", config={"log_level": "DEBUG"})
    assert logger.config["log_level"] == "DEBUG"
    assert logger.config["log_dir"] == "logs"  # 기본값 유지


@pytest.mark.unit
def test_log_dir_created_if_missing(tmp_path):
    """log_dir 경로가 존재하지 않아도 setup_logging() 호출 시 자동으로 생성된다."""
    log_dir = tmp_path / "nested" / "logs"
    logger = StructuredLogger(name="app", config={"log_dir": str(log_dir)})
    logger.setup_logging()
    assert log_dir.exists()


@pytest.mark.unit
def test_log_dir_already_exists_no_error(tmp_path):
    """log_dir이 이미 존재할 때 setup_logging()을 호출해도 예외가 발생하지 않는다."""
    logger = StructuredLogger(name="app", config={"log_dir": str(tmp_path)})
    logger.setup_logging()
    logger.setup_logging()  # 두 번 호출해도 예외 없음


# ---------------------------------------------------------------------------
# structlog.testing
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_info_event_captured_after_setup(tmp_path):
    """setup_logging() 후 info 이벤트가 올바른 구조로 캡처된다."""
    StructuredLogger(name="app", config={"log_dir": str(tmp_path)}).setup_logging()

    with capture_logs() as cap_logs:
        structlog.get_logger().info("login", user_id=42)

    assert cap_logs == [{"event": "login", "user_id": 42, "log_level": "info"}]


@pytest.mark.unit
def test_debug_filtered_at_info_level(tmp_path):
    """log_level=INFO 설정 시 DEBUG 이벤트는 캡처되지 않는다."""
    StructuredLogger(name="app", config={"log_dir": str(tmp_path), "log_level": "INFO"}).setup_logging()

    with capture_logs() as cap_logs:
        log = structlog.get_logger()
        log.debug("debug message")
        log.info("info message")

    assert len(cap_logs) == 1
    assert cap_logs[0]["log_level"] == "info"


@pytest.mark.unit
def test_exception_includes_exception_field(tmp_path):
    """except 블록에서 exception() 호출 시 dict_tracebacks가 exception 필드를 구조체로 변환한다."""
    StructuredLogger(name="app", config={"log_dir": str(tmp_path)}).setup_logging()

    cap = LogCapture()
    structlog.configure(processors=[structlog.processors.dict_tracebacks, cap])

    try:
        raise ValueError("boom")
    except ValueError:
        structlog.get_logger().exception("unexpected error")

    entry = cap.entries[0]
    assert entry["log_level"] == "error"
    assert "exc_info" not in entry
    assert entry["exception"][0]["exc_type"] == "ValueError"
    assert entry["exception"][0]["exc_value"] == "boom"


@pytest.mark.unit
def test_merge_contextvars(tmp_path):
    """bind_contextvars()로 바인딩된 컨텍스트가 이후 모든 이벤트에 자동으로 병합된다."""
    StructuredLogger(name="app", config={"log_dir": str(tmp_path)})

    structlog.contextvars.bind_contextvars(request_id="req-123")
    cap = LogCapture()
    structlog.configure(processors=[structlog.contextvars.merge_contextvars, cap])

    try:
        structlog.get_logger().info("request received")
    finally:
        structlog.contextvars.clear_contextvars()

    assert cap.entries[0]["event"] == "request received"
    assert cap.entries[0]["request_id"] == "req-123"


@pytest.mark.unit
def test_positional_arguments_formatted(tmp_path):
    """%s 스타일 포맷 인자가 event 문자열로 치환된다."""
    StructuredLogger(name="app", config={"log_dir": str(tmp_path)})

    cap = LogCapture()
    structlog.configure(processors=[structlog.stdlib.PositionalArgumentsFormatter(), cap])

    structlog.get_logger().info("user %s logged in", "alice")

    assert cap.entries[0]["event"] == "user alice logged in"


@pytest.mark.unit
def test_stack_info_rendered(tmp_path):
    """stack_info=True 전달 시 현재 콜스택이 문자열로 변환된다."""
    StructuredLogger(name="app", config={"log_dir": str(tmp_path)})

    cap = LogCapture()
    structlog.configure(processors=[structlog.processors.StackInfoRenderer(), cap])

    structlog.get_logger().info("trace this", stack_info=True)

    entry = cap.entries[0]
    assert isinstance(entry.get("stack"), str)
    assert "test_stack_info_rendered" in entry["stack"]


@pytest.mark.unit
def test_set_exc_info_adds_exc_info_on_exception_call(tmp_path):
    """log.exception() 호출 시 set_exc_info가 exc_info=True를 이벤트에 주입한다."""
    StructuredLogger(name="app", config={"log_dir": str(tmp_path)})

    cap = LogCapture()
    structlog.configure(processors=[structlog.dev.set_exc_info, cap])

    try:
        raise ValueError("boom")
    except ValueError:
        structlog.get_logger().exception("something failed")

    assert cap.entries[0]["exc_info"] is True


@pytest.mark.unit
def test_set_exc_info_not_added_for_error_call(tmp_path):
    """log.error() 호출 시 set_exc_info는 exc_info를 주입하지 않는다."""
    StructuredLogger(name="app", config={"log_dir": str(tmp_path)})

    cap = LogCapture()
    structlog.configure(processors=[structlog.dev.set_exc_info, cap])

    try:
        raise ValueError("boom")
    except ValueError:
        structlog.get_logger().error("something failed")

    assert "exc_info" not in cap.entries[0]


@pytest.mark.unit
def test_add_logger_name(tmp_path):
    """add_logger_name이 logger 필드에 로거 이름을 자동 추가한다."""
    StructuredLogger(name="app", config={"log_dir": str(tmp_path)}).setup_logging()

    cap = LogCapture()
    structlog.configure(processors=[structlog.stdlib.add_logger_name, cap])

    structlog.get_logger("myservice").info("event")

    assert cap.entries[0]["logger"] == "myservice"


# ---------------------------------------------------------------------------
# Console handler
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_console_handler_added_when_console_in_outputs(tmp_path):
    """outputs에 console이 포함되면 root logger에 StreamHandler가 추가된다."""
    StructuredLogger(name="app", config={"log_dir": str(tmp_path), "outputs": ["console"]})

    handlers = logging.getLogger().handlers
    assert any(type(h) is logging.StreamHandler for h in handlers)


@pytest.mark.unit
def test_console_handler_uses_processor_formatter(tmp_path):
    """console handler의 formatter가 ProcessorFormatter로 설정된다."""
    StructuredLogger(name="app", config={"log_dir": str(tmp_path), "outputs": ["console"]})

    handlers = logging.getLogger().handlers
    stream_handlers = [h for h in handlers if type(h) is logging.StreamHandler]
    assert len(stream_handlers) == 1
    assert isinstance(stream_handlers[0].formatter, structlog.stdlib.ProcessorFormatter)


@pytest.mark.unit
def test_console_handler_not_added_when_outputs_empty(tmp_path):
    """outputs가 빈 리스트이면 root logger에 핸들러가 추가되지 않는다."""
    StructuredLogger(name="app", config={"log_dir": str(tmp_path), "outputs": []})

    handlers = logging.getLogger().handlers
    assert not any(type(h) is logging.StreamHandler for h in handlers)


@pytest.mark.unit
def test_setup_logging_clears_handlers_on_repeated_call(tmp_path):
    """setup_logging()을 여러 번 호출해도 StreamHandler가 중복 추가되지 않는다."""
    logger = StructuredLogger(name="app", config={"log_dir": str(tmp_path), "outputs": ["console"]})
    logger.setup_logging()

    stream_handlers = [h for h in logging.getLogger().handlers if type(h) is logging.StreamHandler]
    assert len(stream_handlers) == 1


# ---------------------------------------------------------------------------
# _get_renderer
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_renderer_console_returns_console_renderer(tmp_path):
    """console 출력 시 ConsoleRenderer(colors=True)를 반환한다."""
    logger = StructuredLogger(name="app", config={"log_dir": str(tmp_path)})
    assert isinstance(logger._get_renderer("console"), structlog.dev.ConsoleRenderer)


@pytest.mark.unit
def test_get_renderer_file_json_returns_json_renderer(tmp_path):
    """file 출력 + format=json 시 JSONRenderer를 반환한다."""
    logger = StructuredLogger(name="app", config={"log_dir": str(tmp_path), "format": "json"})
    assert isinstance(logger._get_renderer("file"), structlog.processors.JSONRenderer)


@pytest.mark.unit
def test_get_renderer_file_text_returns_console_renderer(tmp_path):
    """file 출력 + format=text 시 ConsoleRenderer(colors=False)를 반환한다."""
    logger = StructuredLogger(name="app", config={"log_dir": str(tmp_path), "format": "text"})
    assert isinstance(logger._get_renderer("file"), structlog.dev.ConsoleRenderer)


@pytest.mark.unit
def test_json_renderer_does_not_escape_korean(tmp_path):
    """JSONRenderer가 한글을 \\uXXXX로 이스케이프하지 않고 그대로 직렬화한다."""
    logger = StructuredLogger(name="app", config={"log_dir": str(tmp_path), "format": "json"})
    renderer = logger._get_renderer("file")
    result = renderer(None, None, {"event": "한글 테스트"})
    assert "한글 테스트" in result
    assert "\\u" not in result


# ---------------------------------------------------------------------------
# File handler
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_file_handler_added_when_file_in_outputs(tmp_path):
    """outputs에 file이 포함되면 root logger에 RotatingFileHandler가 추가된다."""
    StructuredLogger(name="app", config={"log_dir": str(tmp_path), "outputs": ["file"]})

    assert any(isinstance(h, RotatingFileHandler) for h in logging.getLogger().handlers)


@pytest.mark.unit
def test_file_handler_uses_processor_formatter(tmp_path):
    """file handler의 formatter가 ProcessorFormatter로 설정된다."""
    StructuredLogger(name="app", config={"log_dir": str(tmp_path), "outputs": ["file"], "error_tracking": False})

    handlers = [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]
    assert len(handlers) == 1
    assert isinstance(handlers[0].formatter, structlog.stdlib.ProcessorFormatter)


@pytest.mark.unit
def test_file_handler_creates_log_file(tmp_path):
    """{name}.log 파일이 log_dir에 생성된다."""
    StructuredLogger(name="myapp", config={"log_dir": str(tmp_path), "outputs": ["file"]})

    assert (tmp_path / "myapp.log").exists()


# ---------------------------------------------------------------------------
# Error tracking handler
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_error_handler_added_when_error_tracking_enabled(tmp_path):
    """error_tracking=True이면 ERROR 전용 RotatingFileHandler가 추가된다."""
    StructuredLogger(name="app", config={"log_dir": str(tmp_path), "outputs": ["file"], "error_tracking": True})

    rotating_handlers = [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]
    assert len(rotating_handlers) == 2  # file handler + error handler


@pytest.mark.unit
def test_error_handler_level_is_error(tmp_path):
    """{name}.error.log 핸들러의 레벨이 ERROR로 설정된다."""
    StructuredLogger(name="app", config={"log_dir": str(tmp_path), "outputs": ["file"], "error_tracking": True})

    rotating_handlers = [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]
    assert any(h.level == logging.ERROR for h in rotating_handlers)


@pytest.mark.unit
def test_error_handler_creates_error_log_file(tmp_path):
    """{name}.error.log 파일이 log_dir에 생성된다."""
    StructuredLogger(name="myapp", config={"log_dir": str(tmp_path), "outputs": ["file"], "error_tracking": True})

    assert (tmp_path / "myapp.error.log").exists()


@pytest.mark.unit
def test_error_handler_not_added_when_error_tracking_disabled(tmp_path):
    """error_tracking=False이면 ERROR 전용 핸들러가 추가되지 않는다."""
    StructuredLogger(name="app", config={"log_dir": str(tmp_path), "outputs": ["file"], "error_tracking": False})

    rotating_handlers = [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]
    assert len(rotating_handlers) == 1  # file handler만 존재
