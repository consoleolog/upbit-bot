import logging


class IgnoreWebsocketDebugFilter(logging.Filter):
    """
    websockets.client 에서 발생하는 DEBUG 레벨 로그를 필터링한다.

    WebSocket 연결 과정에서 발생하는 프레임 송수신, 핸드셰이크 등의
    DEBUG 로그는 운영 중 노이즈에 불과하므로 출력을 억제한다.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """로그 레코드를 필터링한다.

        Args:
            record: 평가할 로그 레코드.

        Returns:
            필터링(출력 제외)할 경우 ``False``, 출력을 허용할 경우 ``True``.
        """
        # websockets.client 로거의 DEBUG 로그만 필터링
        return not (record.name.startswith("websockets.client") and record.levelno == logging.DEBUG)
