import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.app.config import Timeframe
from src.connections.database import Database
from src.exchange.candle import Candle
from src.repositories.indicator_info import IndicatorInfoRepository
from src.repositories.market import MarketRepository
from src.repositories.models import IndicatorInfo, IndicatorType
from src.trading.indicators import Indicators, IndicatorSettings, Line, calculate
from src.trading.market_worker import (
    MAX_MISSED_CANDLES,
    MIN_CANDLES_FOR_SIGNAL,
    MarketWorker,
    ReplayResult,
    judge_candle,
    replay,
)
from src.trading.signals import CrossState, OverState, SignalSettings

MACD_TYPES = (IndicatorType.MACD_SHORT, IndicatorType.MACD_MID, IndicatorType.MACD_LONG)

T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=30)
# 과매도·과매수 기준 사이에서 두 선이 겹쳐 아무 이벤트도 없는 값
FLAT = Line(main=(50.0, 50.0), signal=(50.0, 50.0))


def _indicators(*, rsi: Line = FLAT, stochastic: Line = FLAT, macd: Line = FLAT) -> Indicators:
    return Indicators(macd_short=macd, macd_mid=macd, macd_long=macd, rsi=rsi, stochastic=stochastic)


def _next(
    indicators: Indicators, previous: dict[IndicatorType, IndicatorInfo] | None = None, *, judge: bool = True
) -> dict[IndicatorType, IndicatorInfo]:
    return judge_candle(
        market_id=1,
        candle_time=T1,
        indicators=indicators,
        previous=previous or {},
        settings=SignalSettings(),
        judge=judge,
    )


def _previous(indicator_type: IndicatorType, candle_time: datetime = T0, **states: object) -> IndicatorInfo:
    return IndicatorInfo(
        market_id=1, indicator_type=indicator_type, value=1.0, signal=1.0, candle_time=candle_time, **states
    )  # type: ignore[arg-type]


def test_first_candle_has_latest_values_and_no_states() -> None:
    infos = _next(_indicators(rsi=Line(main=(None, 45.0), signal=(None, None))))

    assert set(infos) == set(IndicatorType)
    rsi = infos[IndicatorType.RSI]
    assert (rsi.market_id, rsi.candle_time, rsi.value, rsi.signal) == (1, T1, 45.0, None)
    assert all(info.over_state is None and info.cross_state is None for info in infos.values())


def test_events_set_state_and_candle_time() -> None:
    golden = Line(main=(1.0, 3.0), signal=(2.0, 2.0))
    infos = _next(
        _indicators(
            rsi=Line(main=(40.0, 30.0), signal=(35.0, 35.0)),
            stochastic=Line(main=(70.0, 80.0), signal=(78.0, 76.0)),
            macd=golden,
        )
    )

    assert (infos[IndicatorType.RSI].over_state, infos[IndicatorType.RSI].over_time) == (OverState.OVER_SOLD, T1)
    assert (infos[IndicatorType.RSI].cross_state, infos[IndicatorType.RSI].cross_time) == (CrossState.DEAD_CROSS, T1)
    assert infos[IndicatorType.STOCHASTIC].over_state == OverState.OVER_BOUGHT
    assert infos[IndicatorType.STOCHASTIC].cross_state == CrossState.GOLDEN_CROSS
    for macd_type in (IndicatorType.MACD_SHORT, IndicatorType.MACD_MID, IndicatorType.MACD_LONG):
        assert infos[macd_type].cross_state == CrossState.GOLDEN_CROSS
        assert infos[macd_type].over_state is None


def test_stochastic_over_needs_both_lines() -> None:
    infos = _next(_indicators(stochastic=Line(main=(20.0, 20.0), signal=(30.0, 30.0))))

    assert infos[IndicatorType.STOCHASTIC].over_state is None


def test_no_event_keeps_previous_states() -> None:
    previous = {
        IndicatorType.RSI: _previous(
            IndicatorType.RSI,
            over_state=OverState.OVER_SOLD,
            over_time=T0,
            cross_state=CrossState.GOLDEN_CROSS,
            cross_time=T0,
        )
    }

    rsi = _next(_indicators(rsi=Line(main=(50.0, 51.0), signal=(45.0, 46.0))), previous)[IndicatorType.RSI]

    assert (rsi.value, rsi.candle_time) == (51.0, T1)
    assert (rsi.over_state, rsi.over_time, rsi.cross_state, rsi.cross_time) == (
        OverState.OVER_SOLD,
        T0,
        CrossState.GOLDEN_CROSS,
        T0,
    )


def test_new_event_replaces_only_its_state() -> None:
    previous = {
        IndicatorType.RSI: _previous(
            IndicatorType.RSI,
            over_state=OverState.OVER_SOLD,
            over_time=T0,
            cross_state=CrossState.DEAD_CROSS,
            cross_time=T0,
        )
    }

    rsi = _next(_indicators(rsi=Line(main=(40.0, 65.0), signal=(45.0, 50.0))), previous)[IndicatorType.RSI]

    assert (rsi.over_state, rsi.over_time) == (OverState.OVER_BOUGHT, T1)
    assert (rsi.cross_state, rsi.cross_time) == (CrossState.GOLDEN_CROSS, T1)


def test_without_judging_values_update_but_states_stay() -> None:
    previous = {IndicatorType.RSI: _previous(IndicatorType.RSI, cross_state=CrossState.DEAD_CROSS, cross_time=T0)}

    rsi = _next(_indicators(rsi=Line(main=(40.0, 20.0), signal=(30.0, 30.0))), previous, judge=False)[IndicatorType.RSI]

    assert (rsi.value, rsi.candle_time) == (20.0, T1)
    assert (rsi.over_state, rsi.cross_state, rsi.cross_time) == (None, CrossState.DEAD_CROSS, T0)


DEAD = Line(main=(2.0, 1.0), signal=(1.5, 1.5))


def _rsi_and_stochastic_with_states() -> dict[IndicatorType, IndicatorInfo]:
    states = {
        "over_state": OverState.OVER_SOLD,
        "over_time": T0,
        "cross_state": CrossState.GOLDEN_CROSS,
        "cross_time": T0,
    }
    return {t: _previous(t, **states) for t in (IndicatorType.RSI, IndicatorType.STOCHASTIC)}


def test_macd_all_dead_resets_rsi_and_stochastic_states() -> None:
    infos = _next(_indicators(macd=DEAD), _rsi_and_stochastic_with_states())

    for indicator_type in (IndicatorType.RSI, IndicatorType.STOCHASTIC):
        info = infos[indicator_type]
        assert (info.over_state, info.over_time, info.cross_state, info.cross_time) == (None, None, None, None)
        # 지표 값은 그대로 갱신한다
        assert (info.value, info.candle_time) == (50.0, T1)
    assert all(infos[t].cross_state == CrossState.DEAD_CROSS for t in MACD_TYPES)


def test_macd_reset_when_last_one_turns_dead() -> None:
    previous = _rsi_and_stochastic_with_states() | {
        IndicatorType.MACD_SHORT: _previous(IndicatorType.MACD_SHORT, cross_state=CrossState.DEAD_CROSS, cross_time=T0),
        IndicatorType.MACD_MID: _previous(IndicatorType.MACD_MID, cross_state=CrossState.DEAD_CROSS, cross_time=T0),
        IndicatorType.MACD_LONG: _previous(IndicatorType.MACD_LONG, cross_state=CrossState.GOLDEN_CROSS, cross_time=T0),
    }

    # 이미 데드크로스였던 둘은 상태가 그대로이고, MACD_LONG만 골든크로스에서 데드크로스로 바뀐다
    infos = _next(_indicators(macd=DEAD), previous)

    assert infos[IndicatorType.RSI].cross_state is None
    assert infos[IndicatorType.STOCHASTIC].over_state is None


def test_macd_staying_dead_does_not_reset_again() -> None:
    dead = {"cross_state": CrossState.DEAD_CROSS, "cross_time": T0}
    previous = _rsi_and_stochastic_with_states() | {t: _previous(t, **dead) for t in MACD_TYPES}

    infos = _next(_indicators(macd=DEAD), previous)

    assert infos[IndicatorType.RSI].over_state == OverState.OVER_SOLD
    assert infos[IndicatorType.STOCHASTIC].cross_state == CrossState.GOLDEN_CROSS


def test_macd_partly_dead_does_not_reset() -> None:
    previous = _rsi_and_stochastic_with_states() | {
        IndicatorType.MACD_LONG: _previous(IndicatorType.MACD_LONG, cross_state=CrossState.GOLDEN_CROSS, cross_time=T0)
    }
    # MACD_SHORT, MACD_MID만 데드크로스. MACD_LONG은 골든크로스 상태를 유지
    indicators = Indicators(macd_short=DEAD, macd_mid=DEAD, macd_long=FLAT, rsi=FLAT, stochastic=FLAT)

    infos = _next(indicators, previous)

    assert infos[IndicatorType.RSI].over_state == OverState.OVER_SOLD


def test_without_judging_macd_does_not_reset() -> None:
    infos = _next(_indicators(macd=DEAD), _rsi_and_stochastic_with_states(), judge=False)

    assert infos[IndicatorType.RSI].over_state == OverState.OVER_SOLD


class FakeUpbit:
    def __init__(self, candles: list[Candle]) -> None:
        self.candles = candles
        self.calls: list[tuple[str, Timeframe, datetime]] = []

    async def get_candles(self, market: str, timeframe: Timeframe, to: datetime) -> list[Candle]:
        self.calls.append((market, timeframe, to))
        return self.candles


def _candles(market: str, count: int, close: datetime) -> list[Candle]:
    # 오르내리는 종가. 고가·저가는 종가 ±1
    closes = [Decimal(100 + (i * 7) % 23) for i in range(count)]
    return [
        Candle(market=market, start=close - timedelta(minutes=30 * (count - i)), open=c, high=c + 1, low=c - 1, close=c)
        for i, c in enumerate(closes)
    ]


CLOSE = datetime(2026, 1, 10, 0, 0, tzinfo=UTC)
THIRTY = timedelta(minutes=30)


def _replay(candles: list[Candle], previous: dict[IndicatorType, IndicatorInfo] | None = None) -> ReplayResult:
    return replay(
        market_id=1,
        candles=candles,
        previous=previous or {},
        timeframe=Timeframe.MINUTE_30,
        indicator_settings=IndicatorSettings(),
        signal_settings=SignalSettings(),
    )


def _replay_by_hand(
    candles: list[Candle], start: int, previous: dict[IndicatorType, IndicatorInfo]
) -> dict[IndicatorType, IndicatorInfo]:
    """기준: start번째 캔들부터 마지막까지, 각 캔들까지의 구간으로 차례로 판정한다."""
    states: Mapping[IndicatorType, IndicatorInfo] = previous
    for i in range(start, len(candles)):
        window = candles[: i + 1]
        states = judge_candle(
            market_id=1,
            candle_time=window[-1].start,
            indicators=calculate(window, IndicatorSettings()),
            previous=states,
            settings=SignalSettings(),
            judge=len(window) >= MIN_CANDLES_FOR_SIGNAL,
        )
    return states


def _done_at(candle_time: datetime) -> dict[IndicatorType, IndicatorInfo]:
    """candle_time까지 처리한 이전 상태. RSI에 지난 신호가 남아 있다."""
    return {
        t: _previous(t, candle_time)
        if t is not IndicatorType.RSI
        else _previous(t, candle_time, cross_state=CrossState.DEAD_CROSS, cross_time=candle_time)
        for t in IndicatorType
    }


def test_replay_first_time_judges_only_last_candle() -> None:
    candles = _candles("KRW-BTC", 200, CLOSE)

    result = _replay(candles)

    assert (result.judged, result.reset) == (1, False)
    assert {info.indicator_type: info for info in result.infos} == _replay_by_hand(candles, 199, {})


def test_replay_next_candle_judges_one() -> None:
    candles = _candles("KRW-BTC", 200, CLOSE)
    previous = _done_at(candles[-2].start)

    result = _replay(candles, previous)

    assert result.judged == 1
    assert {info.indicator_type: info for info in result.infos} == _replay_by_hand(candles, 199, previous)


@pytest.mark.parametrize("missed", [3, MAX_MISSED_CANDLES])
def test_replay_missed_candles_oldest_first(missed: int) -> None:
    candles = _candles("KRW-BTC", 200, CLOSE)
    previous = _done_at(candles[-missed - 1].start)

    result = _replay(candles, previous)

    assert (result.judged, result.reset) == (missed, False)
    infos = {info.indicator_type: info for info in result.infos}
    assert infos == _replay_by_hand(candles, 200 - missed, previous)
    assert all(info.candle_time == candles[-1].start for info in infos.values())


def test_replay_too_many_missed_resets_states() -> None:
    candles = _candles("KRW-BTC", 200, CLOSE)
    previous = _done_at(candles[-MAX_MISSED_CANDLES - 2].start)

    result = _replay(candles, previous)

    assert (result.judged, result.reset) == (1, True)
    # 지난 신호(RSI 데드크로스)는 남지 않고, 마지막 캔들만 새로 판정한다
    assert {info.indicator_type: info for info in result.infos} == _replay_by_hand(candles, 199, {})


def test_replay_counts_missed_by_time_not_by_received_candles() -> None:
    # 거래가 없어 캔들이 드문드문 온다. 받은 캔들은 적지만 시각으로는 50개를 넘게 놓쳤다
    candles = _candles("KRW-BTC", 200, CLOSE)
    sparse = [*candles[:150], candles[-1]]
    previous = _done_at(candles[-MAX_MISSED_CANDLES - 2].start)

    assert _replay(sparse, previous).reset


@pytest.mark.parametrize("offset", [0, 1])
def test_replay_already_processed_does_nothing(offset: int) -> None:
    candles = _candles("KRW-BTC", 200, CLOSE)

    result = _replay(candles, _done_at(candles[-1].start + THIRTY * offset))

    assert (result.infos, result.judged, result.reset) == ((), 0, False)


def _worker(upbit: FakeUpbit, database: Database, market_ids: dict[str, int]) -> MarketWorker:
    return MarketWorker(
        upbit=upbit,
        database=database,
        timeframe=Timeframe.MINUTE_30,
        indicator_settings=IndicatorSettings(),
        signal_settings=SignalSettings(),
        market_ids=market_ids,
    )


async def test_no_candles_does_not_touch_database(caplog: pytest.LogCaptureFixture) -> None:
    database = MagicMock()

    await _worker(FakeUpbit([]), database, {"KRW-BTC": 1}).candle_close_job("KRW-BTC", T1)()

    database.transaction.assert_not_called()
    assert "no closed candles" in caplog.text


async def test_unknown_market_fails_before_fetching() -> None:
    upbit = FakeUpbit([])

    with pytest.raises(KeyError):
        await _worker(upbit, MagicMock(), {}).candle_close_job("KRW-BTC", T1)()

    assert upbit.calls == []


async def test_missing_just_closed_candle_is_warned(caplog: pytest.LogCaptureFixture) -> None:
    close = datetime(2026, 1, 10, 0, 0, tzinfo=UTC)
    # 마지막 캔들이 방금 마감된 캔들(23:30)이 아니라 그 전 캔들(23:00)이다
    candles = _candles("KRW-BTC", 10, close - timedelta(minutes=30))
    database = MagicMock()
    database.transaction.side_effect = RuntimeError("stop before database")

    with pytest.raises(RuntimeError, match="stop before database"):
        await _worker(FakeUpbit(candles), database, {"KRW-BTC": 1}).candle_close_job("KRW-BTC", close)()

    assert "just-closed candle missing (market=KRW-BTC, expected=2026-01-09T23:30:00+00:00" in caplog.text
    assert "last=2026-01-09T23:00:00+00:00" in caplog.text


# ---------------------------------------------------------------------------
# 실제 PostgreSQL (TEST_DB_* 가 없으면 건너뜀)
# ---------------------------------------------------------------------------


@pytest.fixture
async def market(database: Database) -> AsyncIterator[tuple[str, int]]:
    """커밋된 테스트용 마켓. 테스트가 끝나면 지운다."""
    symbol = "KRW-T" + uuid.uuid4().hex[:8].upper()
    async with database.transaction() as conn:
        added = await MarketRepository().add(conn, symbol)
    assert added is not None
    yield symbol, added.market_id
    async with database.transaction() as conn:
        await conn.execute("DELETE FROM indicator_info WHERE market_id = $1", added.market_id)
        await conn.execute("DELETE FROM market WHERE market_id = $1", added.market_id)


async def _saved(database: Database, market_id: int) -> dict[IndicatorType, IndicatorInfo]:
    async with database.transaction() as conn:
        return await IndicatorInfoRepository().list_by_market(conn, market_id)


@pytest.mark.integration
async def test_job_saves_indicators_and_signals(database: Database, market: tuple[str, int]) -> None:
    symbol, market_id = market
    close = datetime(2026, 1, 10, 0, 0, tzinfo=UTC)
    candles = _candles(symbol, 200, close)
    upbit = FakeUpbit(candles)

    await _worker(upbit, database, {symbol: market_id}).candle_close_job(symbol, close)()

    # 마감 시각을 to로 넘겨 마감된 캔들만 받는다
    assert upbit.calls == [(symbol, Timeframe.MINUTE_30, close)]
    expected = judge_candle(
        market_id=market_id,
        candle_time=candles[-1].start,
        indicators=calculate(candles, IndicatorSettings()),
        previous={},
        settings=SignalSettings(),
        judge=True,
    )
    assert await _saved(database, market_id) == expected


@pytest.mark.integration
async def test_same_close_twice_does_not_change_saved_state(database: Database, market: tuple[str, int]) -> None:
    symbol, market_id = market
    close = datetime(2026, 1, 10, 0, 0, tzinfo=UTC)
    worker = _worker(FakeUpbit(_candles(symbol, 200, close)), database, {symbol: market_id})
    await worker.candle_close_job(symbol, close)()
    first = await _saved(database, market_id)

    await worker.candle_close_job(symbol, close)()

    assert await _saved(database, market_id) == first


@pytest.mark.integration
async def test_too_few_candles_updates_values_but_keeps_states(database: Database, market: tuple[str, int]) -> None:
    symbol, market_id = market
    close = datetime(2026, 1, 10, 0, 0, tzinfo=UTC)
    earlier = close - timedelta(hours=2)
    async with database.transaction() as conn:
        await IndicatorInfoRepository().save(
            conn,
            IndicatorInfo(
                market_id=market_id,
                indicator_type=IndicatorType.RSI,
                value=10.0,
                signal=10.0,
                candle_time=earlier,
                over_state=OverState.OVER_BOUGHT,
                over_time=earlier,
            ),
        )
    candles = _candles(symbol, MIN_CANDLES_FOR_SIGNAL - 1, close)

    await _worker(FakeUpbit(candles), database, {symbol: market_id}).candle_close_job(symbol, close)()

    rsi = (await _saved(database, market_id))[IndicatorType.RSI]
    assert rsi.candle_time == candles[-1].start
    assert rsi.value == calculate(candles, IndicatorSettings()).rsi.main[-1]
    assert (rsi.over_state, rsi.over_time, rsi.cross_state) == (OverState.OVER_BOUGHT, earlier, None)


@pytest.mark.integration
async def test_job_replays_missed_candles(database: Database, market: tuple[str, int]) -> None:
    symbol, market_id = market
    candles = _candles(symbol, 200, CLOSE)
    done = candles[-4].start
    async with database.transaction() as conn:
        for info in _done_at(done).values():
            await IndicatorInfoRepository().save(conn, replace(info, market_id=market_id))

    await _worker(FakeUpbit(candles), database, {symbol: market_id}).candle_close_job(symbol, CLOSE)()

    previous = {t: replace(info, market_id=market_id) for t, info in _done_at(done).items()}
    expected = _replay_by_hand(candles, 197, previous)
    assert await _saved(database, market_id) == {t: replace(info, market_id=market_id) for t, info in expected.items()}
