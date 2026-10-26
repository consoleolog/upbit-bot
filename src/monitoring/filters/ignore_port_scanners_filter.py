import logging
from typing import ClassVar


class IgnorePortScannersFilter(logging.Filter):
    """
    aiohttp 에서 발생하는 무해한 포트 스캐너 오류를 필터링한다.

    SSL, SSH, SOCKS 등 비 HTTP 프로토콜로 HTTP 포트에 접근하는
    봇/스캐너에 의해 발생하는 오류로, 완전히 무해하며 로그 노이즈에 불과하다.
    """

    # 필터링할 메시지 패턴
    IGNORED_PATTERNS: ClassVar[list[str]] = [
        "BadStatusLine",  # 잘못된 HTTP 요청
        "Invalid method encountered",  # 비 HTTP 프로토콜 시도
        "Error handling request",  # aiohttp 범용 오류 래퍼
    ]

    # 포트 스캐너가 전송하는 바이트 패턴 (16진수 표현)
    SCANNER_BYTES: ClassVar[list[str]] = [
        "\\x16\\x03\\x01",  # SSL/TLS 핸드셰이크
        "\\x04\\x01",  # SOCKS4 요청
        "\\x05\\x01",  # SOCKS5 요청
        "SSH-2.0",  # SSH 스캐너
        "GET / HTTP",  # 포트 스캐너에서 자주 사용되는 HTTP 요청
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        """로그 레코드를 필터링한다.

        Args:
            record: 평가할 로그 레코드.

        Returns:
            필터링(출력 제외)할 경우 ``False``, 출력을 허용할 경우 ``True``.
        """
        # aiohttp.server 로거의 로그만 필터링 대상
        if "aiohttp.server" not in record.name:
            return True  # 그 외 로거는 모두 허용

        # 로그 메시지 추출
        message = record.getMessage() if hasattr(record, "getMessage") else str(record.msg)

        # 포트 스캐너 오류 여부 확인
        for pattern in self.IGNORED_PATTERNS:
            if pattern in message:
                # 스캐너 바이트 패턴이 포함된 경우 필터링
                for scanner_byte in self.SCANNER_BYTES:
                    if scanner_byte in message:
                        # 포트 스캐너 요청으로 판단 — 필터링
                        return False

                # "Invalid method" + 바이트 시퀀스 조합도 필터링
                if "Invalid method" in message and ("b'" in message or 'b"' in message):
                    return False

        # 그 외 aiohttp 오류는 모두 허용
        return True
