import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from src.app.application import Job
from src.app.config import Timeframe
from src.connections.database import Database
from src.exchange.candle import Candle
from src.repositories.indicator_info import IndicatorInfoRepository
from src.repositories.models import IndicatorInfo, IndicatorType
from src.trading.indicators import Indicators, IndicatorSettings, Line, calculate
from src.trading.signals import CrossState, SignalSettings, Threshold, cross, over

logger = logging.getLogger(__name__)

# 계산 구간이 이보다 짧으면 지표 초기 값의 차이가 커서 신호를 갱신하지 않는다 (매매 로직 3.0)
MIN_CANDLES_FOR_SIGNAL = 150
# 놓친 캔들이 이보다 많으면 그 사이의 신호는 이미 지난 것으로 보고 상태를 비운다 (매매 로직 3.0)
MAX_MISSED_CANDLES = 50

MACD_TYPES = (IndicatorType.MACD_SHORT, IndicatorType.MACD_MID, IndicatorType.MACD_LONG)


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
        async with self._database.transaction() as conn:
            previous = await self._indicator_infos.list_by_market(conn, market_id)
            result = replay(
                market_id=market_id,
                candles=candles,
                previous=previous,
                timeframe=self._timeframe,
                indicator_settings=self._indicator_settings,
                signal_settings=self._signal_settings,
            )
            saved = [info for info in result.infos if await self._indicator_infos.save(conn, info) is not None]
        # 커밋된 뒤에 기록한다
        if result.reset:
            logger.warning("missed more than %d candles; signal states reset (market=%s)", MAX_MISSED_CANDLES, market)
        logger.info(
            "candle close (market=%s, close=%s, candles=%d, last=%s, judged=%d, saved=%d) %s",
            market,
            close.isoformat(),
            len(candles),
            candles[-1].start_kst.isoformat(),
            result.judged,
            len(saved),
            " ".join(_describe(info) for info in saved),
        )


@dataclass(frozen=True, kw_only=True)
class ReplayResult:
    infos: tuple[IndicatorInfo, ...]
    # 판정한 캔들 수. 놓친 캔들을 다시 판정하면 2 이상이다
    judged: int
    # 놓친 캔들이 너무 많아 신호 상태를 비웠는지
    reset: bool


def replay(
    *,
    market_id: int,
    candles: Sequence[Candle],
    previous: Mapping[IndicatorType, IndicatorInfo],
    timeframe: Timeframe,
    indicator_settings: IndicatorSettings,
    signal_settings: SignalSettings,
) -> ReplayResult:
    """마지막으로 처리한 캔들 이후의 마감 캔들을 오래된 것부터 하나씩 판정한다 (매매 로직 3.0).

    처음 처리하면 마지막 캔들만 판정한다. 놓친 캔들이 MAX_MISSED_CANDLES를 넘으면 신호 상태를 비우고 마지막 캔들만 판정한다.
    각 캔들의 지표는 받은 캔들 중 그 캔들까지의 구간으로 계산한다. 이미 모두 처리했으면 infos는 비어 있다.
    """
    last_done = max((info.candle_time for info in previous.values()), default=None)
    latest = candles[-1].start
    if last_done is not None and last_done >= latest:
        return ReplayResult(infos=(), judged=0, reset=False)
    # 거래가 없는 구간은 캔들이 없으므로 받은 개수가 아니라 시각 차이로 센다
    reset = last_done is not None and (latest - last_done) // timeframe.duration > MAX_MISSED_CANDLES
    if last_done is None or reset:
        states: Mapping[IndicatorType, IndicatorInfo] = {}
        targets = [len(candles) - 1]
    else:
        states = previous
        targets = [i for i, candle in enumerate(candles) if candle.start > last_done]
    for i in targets:
        window = candles[: i + 1]
        states = judge_candle(
            market_id=market_id,
            candle_time=window[-1].start,
            indicators=calculate(window, indicator_settings),
            previous=states,
            settings=signal_settings,
            judge=len(window) >= MIN_CANDLES_FOR_SIGNAL,
        )
    return ReplayResult(infos=tuple(states.values()), judged=len(targets), reset=reset)


def judge_candle(
    *,
    market_id: int,
    candle_time: datetime,
    indicators: Indicators,
    previous: Mapping[IndicatorType, IndicatorInfo],
    settings: SignalSettings,
    judge: bool,
) -> dict[IndicatorType, IndicatorInfo]:
    """캔들 하나(candle_time)의 지표 값과 신호 상태 (매매 로직 4.1, 7).

    이벤트가 없거나 판정하지 않으면(judge=False) 이전 상태를 그대로 둔다.
    MACD 3종이 모두 데드크로스가 되면 RSI와 스토캐스틱의 신호 상태를 비운다.
    """
    # 지표 타입별 (값 목록, 과매도/과매수 기준, 과매도/과매수 판정에 시그널선도 쓰는지)
    sources: dict[IndicatorType, tuple[Line, Threshold | None, bool]] = {
        IndicatorType.RSI: (indicators.rsi, settings.rsi, False),
        IndicatorType.STOCHASTIC: (indicators.stochastic, settings.stochastic, True),
        IndicatorType.MACD_SHORT: (indicators.macd_short, None, False),
        IndicatorType.MACD_MID: (indicators.macd_mid, None, False),
        IndicatorType.MACD_LONG: (indicators.macd_long, None, False),
    }
    infos: dict[IndicatorType, IndicatorInfo] = {}
    for indicator_type, (line, threshold, with_signal) in sources.items():
        before = previous.get(indicator_type)
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
        infos[indicator_type] = info
    if _macd_turned_all_dead(previous, infos):
        for indicator_type in (IndicatorType.RSI, IndicatorType.STOCHASTIC):
            infos[indicator_type] = replace(
                infos[indicator_type], over_state=None, over_time=None, cross_state=None, cross_time=None
            )
    return infos


def _macd_turned_all_dead(
    previous: Mapping[IndicatorType, IndicatorInfo], current: Mapping[IndicatorType, IndicatorInfo]
) -> bool:
    """MACD 3종이 모두 데드크로스이고, 그중 하나라도 이번 캔들에서 데드크로스가 됐는지 (매매 로직 7).

    데드크로스가 이어지는 동안 매 캔들 초기화를 반복하지 않기 위해 바뀐 경우만 본다.
    """
    dead = CrossState.DEAD_CROSS
    if any(current[t].cross_state != dead for t in MACD_TYPES):
        return False
    return any(t not in previous or previous[t].cross_state != dead for t in MACD_TYPES)


def _describe(info: IndicatorInfo) -> str:
    """로그용 요약. 값은 유효숫자로 적어, 가격이 낮은 코인의 작은 MACD 값도 0으로 보이지 않게 한다."""

    def number(value: float | None) -> str:
        return "None" if value is None else f"{value:.6g}"

    return (
        f"{info.indicator_type}={number(info.value)}/{number(info.signal)}"
        f"[over={info.over_state}, cross={info.cross_state}]"
    )
