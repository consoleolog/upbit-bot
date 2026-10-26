import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from src.application import Application

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_STARTUP_FAILED = 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="upbit-bot", description="업비트 자동매매 봇")
    parser.add_argument("--config", type=Path, required=True, help="설정 파일 경로")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)

    # 잘못된 설정은 매매를 시작하기 전에 막는다
    if not args.config.is_file():
        logger.error("config file not found: %s", args.config)
        return EXIT_STARTUP_FAILED

    asyncio.run(Application(args.config).run())
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
