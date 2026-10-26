import json
import logging
import sys
from functools import partial
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog

from .filters import IgnorePortScannersFilter, IgnoreWebsocketDebugFilter


class StructuredLogger:
    def __init__(self, name: str = "root", config: dict | None = None):
        self.name = name

        default_config = self._default_config()

        if config:
            default_config.update(config)
        self.config = default_config
        self.setup_logging()

    @staticmethod
    def _default_config() -> dict:
        return {
            "log_level": "INFO",
            "log_dir": "logs",
            "outputs": ["console", "file"],
            "max_file_size": 10 * 1024 * 1024,  # 10MB
            "backup_count": 10,
            "format": "json",  # json or text
            "error_tracking": True,
        }

    def _get_renderer(self, output_type: str):
        """출력 유형과 format 설정에 따라 적절한 렌더러를 반환한다.

        console: ConsoleRenderer(colors=True) — format 설정과 무관하게 컬러 출력
        file (format=json): JSONRenderer — PLG 스택 수집에 적합한 구조화 JSON
        file (format=text): ConsoleRenderer(colors=False) — 사람이 읽기 쉬운 텍스트
        """
        if output_type == "console":
            return structlog.dev.ConsoleRenderer(colors=True)
        if self.config.get("format") == "json":
            return structlog.processors.JSONRenderer(serializer=partial(json.dumps, ensure_ascii=False))
        return structlog.dev.ConsoleRenderer(colors=False)

    def setup_logging(self):
        log_dir = Path(self.config.get("log_dir", "logs"))
        log_dir.mkdir(parents=True, exist_ok=True)

        level = getattr(logging, self.config.get("log_level", "INFO").upper(), logging.INFO)

        shared_processors = [
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso", utc=False),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.dict_tracebacks,
        ]

        structlog.configure(
            processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
            wrapper_class=structlog.make_filtering_bound_logger(level),
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )

        root_logger = logging.getLogger()
        root_logger.setLevel(level)

        # 기존에 있던 handlers 삭제
        root_logger.handlers.clear()

        # Console handler
        if "console" in self.config.get("outputs", []):
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(
                structlog.stdlib.ProcessorFormatter(
                    processors=[
                        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                        self._get_renderer("console"),
                    ],
                    foreign_pre_chain=shared_processors,
                )
            )
            console_handler.addFilter(IgnorePortScannersFilter())
            console_handler.addFilter(IgnoreWebsocketDebugFilter())
            root_logger.addHandler(console_handler)

        # File handler
        if "file" in self.config.get("outputs", []):
            file_handler = RotatingFileHandler(
                log_dir / f"{self.name}.log",
                maxBytes=self.config.get("max_file_size", 10 * 1024 * 1024),
                backupCount=self.config.get("backup_count", 10),
                encoding="utf-8",
            )
            file_handler.setFormatter(
                structlog.stdlib.ProcessorFormatter(
                    processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, self._get_renderer("file")],
                    foreign_pre_chain=shared_processors,
                )
            )
            file_handler.addFilter(IgnorePortScannersFilter())
            file_handler.addFilter(IgnoreWebsocketDebugFilter())
            root_logger.addHandler(file_handler)

            # 에러 로그는 따로 관리
            if self.config.get("error_tracking"):
                error_handler = RotatingFileHandler(
                    log_dir / f"{self.name}.error.log",
                    maxBytes=self.config.get("max_file_size", 10 * 1024 * 1024),
                    backupCount=self.config.get("backup_count", 10),
                    encoding="utf-8",
                )
                error_handler.setLevel(logging.ERROR)
                error_handler.setFormatter(
                    structlog.stdlib.ProcessorFormatter(
                        processors=[
                            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                            self._get_renderer("file"),
                        ],
                        foreign_pre_chain=shared_processors,
                    )
                )
                root_logger.addHandler(error_handler)
