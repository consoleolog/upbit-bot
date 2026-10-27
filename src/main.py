import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol

from src.application import Application, Job
from src.candle import Candle
from src.config import ConfigError, Timeframe, TradingConfig, load_config
from src.indicators import IndicatorSettings, Line, calculate
from src.scheduler import CandleScheduler, JobFactory
from src.upbit_client import UpbitClient

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_STARTUP_FAILED = 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="upbit-bot", description="업비트 자동매매 봇")
    parser.add_argument("--config", type=Path, required=True, help="설정 파일 경로")
    return parser.parse_args(argv)


class CandleSource(Protocol):
    async def get_candles(self, market: str, timeframe: Timeframe, to: datetime) -> list[Candle]: ...


def candle_close_job_factory(upbit: CandleSource, timeframe: Timeframe, settings: IndicatorSettings) -> JobFactory:
    """캔들 마감 작업. 마켓 처리를 구현하기 전까지는 마감된 캔들을 조회해 보조지표를 계산하고 기록만 남긴다."""

    def factory(market: str, close: datetime) -> Job:
        async def job() -> None:
            candles = await upbit.get_candles(market, timeframe, close)
            last = candles[-1] if candles else None
            indicators = calculate(candles, settings)
            logger.info(
                "candle close job (market=%s, close=%s, candles=%d, last=%s, last_close=%s, "
                "rsi=%s, stochastic=%s, macd_short=%s, macd_mid=%s, macd_long=%s)",
                market,
                close.isoformat(),
                len(candles),
                last.start_kst.isoformat() if last else None,
                last.close if last else None,
                _latest(indicators.rsi),
                _latest(indicators.stochastic),
                _latest(indicators.macd_short),
                _latest(indicators.macd_mid),
                _latest(indicators.macd_long),
            )

        return job

    return factory


def _latest(line: Line) -> str:
    """마지막 캔들의 메인선/시그널선 값. 값이 없으면 None으로 적는다."""
    if not line.main:
        return "None/None"
    main, signal = line.main[-1], line.signal[-1]
    # 유효숫자로 적어, 가격이 낮은 코인의 작은 MACD 값도 0으로 보이지 않게 한다
    return f"{'None' if main is None else f'{main:.6g}'}/{'None' if signal is None else f'{signal:.6g}'}"


async def run(config_path: Path, config: TradingConfig) -> None:
    # 연결은 애플리케이션이 작업을 모두 정리한 뒤에 닫는다
    async with UpbitClient() as upbit:
        app = Application(config_path)
        scheduler = CandleScheduler(
            timeframe=config.timeframe,
            markets=config.markets,
            submit=app.submit,
            # 지표 기간은 설정 파일에서 읽기 전까지 매매 로직 2.1의 기본값을 쓴다
            job_factory=candle_close_job_factory(upbit, config.timeframe, IndicatorSettings()),
        )
        app.add_service(f"scheduler:{config.timeframe}", scheduler.run)
        await app.run()


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)

    # 잘못된 설정은 매매를 시작하기 전에 막는다
    try:
        config = load_config(args.config)
    except ConfigError as error:
        logger.error("invalid config: %s", error)
        return EXIT_STARTUP_FAILED

    asyncio.run(run(args.config, config))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
