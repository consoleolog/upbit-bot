import logging
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import Protocol

from src.application import Job
from src.candle import Candle
from src.config import Timeframe
from src.database import Database
from src.indicators import Indicators, IndicatorSettings, Line, calculate
from src.repositories.indicator_info import IndicatorInfoRepository
from src.repositories.models import IndicatorInfo, IndicatorType
from src.signals import SignalSettings, Threshold, cross, over

logger = logging.getLogger(__name__)

# 계산 구간이 이보다 짧으면 지표 초기 값의 차이가 커서 신호를 갱신하지 않는다 (매매 로직 3.0)
MIN_CANDLES_FOR_SIGNAL = 150


class CandleSource(Protocol):
    async def get_candles(self, market: str, timeframe: Timeframe, to: datetime) -> list[Candle]: ...


class MarketWorker:
    """마켓의 캔들 마감 처리. 마감된 캔들로 보조지표를 계산하고 신호를 판정해 DB에 저장한다."""

    def __init__(
        self,
        *,
        upbit: CandleSource,
        database: Database,
        timeframe: Timeframe,
        indicator_settings: IndicatorSettings,
        signal_settings: SignalSettings,
        market_ids: Mapping[str, int],
    ) -> None:
        self._upbit = upbit
        self._database = database
        self._timeframe = timeframe
        self._indicator_settings = indicator_settings
        self._signal_settings = signal_settings
        self._market_ids = market_ids
        self._indicator_infos = IndicatorInfoRepository()

    def candle_close_job(self, market: str, close: datetime) -> Job:
        """스케줄러에 넘기는 작업. 마켓의 close 시각 캔들 마감을 처리한다."""

        async def job() -> None:
            await self.on_candle_close(market, close)

        return job

    async def on_candle_close(self, market: str, close: datetime) -> None:
        market_id = self._market_ids[market]
        # 거래소 호출은 트랜잭션 밖에서 한다
        candles = await self._upbit.get_candles(market, self._timeframe, close)
        if not candles:
            logger.warning("no closed candles (market=%s, close=%s)", market, close.isoformat())
            return
        expected = close - self._timeframe.duration
        if candles[-1].start != expected:
            # 업비트 반영 지연이나 거래 없음으로 방금 마감된 캔들이 빠졌다. 그 캔들의 신호를 놓칠 수 있으므로 알린다
            logger.warning(
                "just-closed candle missing (market=%s, expected=%s, last=%s)",
                market,
                expected.isoformat(),
                candles[-1].start.isoformat(),
            )
        indicators = calculate(candles, self._indicator_settings)
        async with self._database.transaction() as conn:
            previous = await self._indicator_infos.list_by_market(conn, market_id)
            infos = next_indicator_infos(
                market_id=market_id,
                candle_time=candles[-1].start,
                indicators=indicators,
                previous=previous,
                settings=self._signal_settings,
                judge=len(candles) >= MIN_CANDLES_FOR_SIGNAL,
            )
            saved = [info for info in infos if await self._indicator_infos.save(conn, info) is not None]
        # 커밋된 뒤에 기록한다
        logger.info(
            "candle close (market=%s, close=%s, candles=%d, last=%s, saved=%d) %s",
            market,
            close.isoformat(),
            len(candles),
            candles[-1].start_kst.isoformat(),
            len(saved),
            " ".join(_describe(info) for info in saved),
        )


def next_indicator_infos(
    *,
    market_id: int,
    candle_time: datetime,
    indicators: Indicators,
    previous: Mapping[IndicatorType, IndicatorInfo],
    settings: SignalSettings,
    judge: bool,
) -> list[IndicatorInfo]:
    """판단할 캔들(candle_time)의 지표 값과 신호 상태 (매매 로직 4.1).

    이벤트가 없거나 판정하지 않으면(judge=False) 이전 상태를 그대로 둔다.
    이미 이 캔들이나 더 새로운 캔들로 저장된 지표 타입은 다시 만들지 않는다.
    """
    # 지표 타입별 (값 목록, 과매도/과매수 기준, 과매도/과매수 판정에 시그널선도 쓰는지)
    sources: dict[IndicatorType, tuple[Line, Threshold | None, bool]] = {
        IndicatorType.RSI: (indicators.rsi, settings.rsi, False),
        IndicatorType.STOCHASTIC: (indicators.stochastic, settings.stochastic, True),
        IndicatorType.MACD_SHORT: (indicators.macd_short, None, False),
        IndicatorType.MACD_MID: (indicators.macd_mid, None, False),
        IndicatorType.MACD_LONG: (indicators.macd_long, None, False),
    }
    infos = []
    for indicator_type, (line, threshold, with_signal) in sources.items():
        before = previous.get(indicator_type)
        if before is not None and before.candle_time >= candle_time:
            continue
        info = IndicatorInfo(
            market_id=market_id,
            indicator_type=indicator_type,
            value=line.main[-1],
            signal=line.signal[-1],
            candle_time=candle_time,
            over_state=before.over_state if before else None,
            over_time=before.over_time if before else None,
            cross_state=before.cross_state if before else None,
            cross_time=before.cross_time if before else None,
        )
        if judge:
            values = (line.main[-1], line.signal[-1]) if with_signal else (line.main[-1],)
            if threshold is not None and (over_state := over(values, threshold)) is not None:
                info = replace(info, over_state=over_state, over_time=candle_time)
            if (cross_state := cross(line)) is not None:
                info = replace(info, cross_state=cross_state, cross_time=candle_time)
        infos.append(info)
    return infos


def _describe(info: IndicatorInfo) -> str:
    """로그용 요약. 값은 유효숫자로 적어, 가격이 낮은 코인의 작은 MACD 값도 0으로 보이지 않게 한다."""

    def number(value: float | None) -> str:
        return "None" if value is None else f"{value:.6g}"

    return (
        f"{info.indicator_type}={number(info.value)}/{number(info.signal)}"
        f"[over={info.over_state}, cross={info.cross_state}]"
    )
