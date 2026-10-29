"""모의 실행 장기 시뮬레이션 (실제 PostgreSQL).

시드가 고정된 가격 경로로 캔들 마감 400번을 실제 MarketWorker로 차례로 처리하고,
남은 모의 포지션이 매매 로직의 규칙을 지키는지 판단 코드와 독립적으로 캔들 가격만으로 검사한다.
"""

import random
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

import pytest

from src.app.config import ExitSettings, Timeframe
from src.connections.database import Database
from src.exchange.candle import Candle
from src.repositories.market import MarketRepository
from src.repositories.models import PaperPosition, PositionStatus
from src.trading.decision import Action, Holding, Reason, decide, net_profit_rate
from src.trading.indicators import IndicatorSettings, calculate
from src.trading.market_worker import MarketWorker, replay
from src.trading.paper import PAPER_FEE_RATE
from src.trading.signals import SignalSettings

pytestmark = pytest.mark.integration

START = datetime(2026, 1, 1, tzinfo=UTC)
STEP = timedelta(minutes=30)
# 이 시드와 변동성에서는 매수와 매도 사유 4가지(손절·익절·과매수·데드크로스)가 모두 나온다
SEED = 22
SIGMA = 0.004
CANDLES = 600
WINDOW = 200
EXIT = ExitSettings()
ENTRY_MAX = 50.0


def _walk() -> list[Candle]:
    """시드가 고정된 무작위 가격 경로. 매번 같은 캔들이 나온다."""
    rng = random.Random(SEED)
    price = 100.0
    candles = []
    for i in range(CANDLES):
        open_ = price
        price *= 1 + rng.gauss(0, SIGMA)
        high = max(open_, price) * (1 + abs(rng.gauss(0, 0.004)))
        low = min(open_, price) * (1 - abs(rng.gauss(0, 0.004)))
        candles.append(
            Candle(
                market="KRW-SIM",
                start=START + STEP * i,
                open=Decimal(str(round(open_, 2))),
                high=Decimal(str(round(high, 2))),
                low=Decimal(str(round(low, 2))),
                close=Decimal(str(round(price, 2))),
            )
        )
    return candles


class HistoricalUpbit:
    """업비트처럼 to 이전에 마감된 캔들 중 최근 WINDOW개를 오래된 것부터 돌려준다."""

    def __init__(self, candles: list[Candle]) -> None:
        self.candles = candles

    async def get_candles(self, market: str, timeframe: Timeframe, to: datetime) -> list[Candle]:
        closed = [replace(c, market=market) for c in self.candles if c.start + timeframe.duration <= to]
        return closed[-WINDOW:]


def _reference_trades(candles: list[Candle]) -> list[tuple[Action, Reason, datetime]]:
    """DB 없이 메모리에서만 같은 순서로 판단한 기준 결과."""
    states: dict = {}
    holding = None
    trades = []
    for end in range(WINDOW, CANDLES + 1):
        window = candles[end - WINDOW : end]
        result = replay(
            market_id=1,
            candles=window,
            previous=states,
            timeframe=Timeframe.MINUTE_30,
            indicator_settings=IndicatorSettings(),
            signal_settings=SignalSettings(),
        )
        states = {info.indicator_type: info for info in result.infos}
        last = window[-1]
        decision = decide(
            states=states,
            holding=holding,
            candle_time=last.start,
            close=last.close,
            exit_settings=EXIT,
            stochastic_entry_max=ENTRY_MAX,
            sell_fee_rate=PAPER_FEE_RATE,
        )
        if decision is None:
            continue
        trades.append((decision.action, decision.reason, last.start))
        holding = Holding(average_price=last.close * (1 + PAPER_FEE_RATE)) if decision.action is Action.BUY else None
        states = {
            t: replace(info, over_state=None, over_time=None, cross_state=None, cross_time=None)
            for t, info in states.items()
        }
    return trades


def _close_at(candles: list[Candle], start: datetime) -> Decimal:
    return next(c.close for c in candles if c.start == start)


def _rate(price: Decimal, average_price: Decimal) -> Decimal:
    return net_profit_rate(price=price, average_price=average_price, sell_fee_rate=PAPER_FEE_RATE)


def _check_simulation_covers_every_exit_reason(candles: list[Candle], positions: list[PaperPosition]) -> None:

    reasons = {p.exit_reason for p in positions if p.exit_reason is not None}
    assert reasons == {Reason.STOP_LOSS, Reason.TAKE_PROFIT, Reason.OVERBOUGHT_EXIT, Reason.DEAD_CROSS_EXIT}


def _check_trades_match_in_memory_reference(candles: list[Candle], positions: list[PaperPosition]) -> None:

    recorded = []
    for p in positions:
        recorded.append((Action.BUY, Reason.ENTRY, p.entry_time))
        if p.status is PositionStatus.CLOSED:
            recorded.append((Action.SELL, p.exit_reason, p.exit_time))

    # DB·트랜잭션을 거쳐도 메모리에서만 판단한 결과와 같다
    assert recorded == _reference_trades(candles)


def _check_positions_do_not_overlap(candles: list[Candle], positions: list[PaperPosition]) -> None:

    assert sum(p.status is PositionStatus.OPEN for p in positions) <= 1
    assert all(p.status is PositionStatus.CLOSED for p in positions[:-1])
    for previous, current in pairwise(positions):
        assert previous.exit_time is not None and previous.exit_time < current.entry_time


def _check_prices_and_profit_follow_candles(candles: list[Candle], positions: list[PaperPosition]) -> None:

    for p in positions:
        # 판단한 캔들의 종가로 체결됐다고 보고, 평단가는 매수 수수료를 포함한다
        assert p.entry_price == _close_at(candles, p.entry_time)
        assert p.average_price == p.entry_price * (1 + PAPER_FEE_RATE)
        assert (p.take_profit_rate, p.stop_loss_rate, p.min_profit_rate) == (
            EXIT.take_profit_rate,
            EXIT.stop_loss_rate,
            EXIT.min_profit_rate,
        )
        if p.status is PositionStatus.CLOSED:
            assert p.exit_time is not None and p.exit_price is not None
            assert p.exit_price == _close_at(candles, p.exit_time)
            assert p.net_profit_rate == _rate(p.exit_price, p.average_price)


def _check_exit_reason_matches_profit_range(candles: list[Candle], positions: list[PaperPosition]) -> None:

    for p in (p for p in positions if p.status is PositionStatus.CLOSED):
        rate = p.net_profit_rate
        assert rate is not None
        if p.exit_reason is Reason.STOP_LOSS:
            assert rate <= -p.stop_loss_rate
        elif p.exit_reason is Reason.TAKE_PROFIT:
            assert rate >= p.take_profit_rate
        else:
            # 신호 매도는 순수익일 때만 하고, 손절·익절 범위 밖이다
            assert p.min_profit_rate < rate < p.take_profit_rate


def _check_stop_loss_and_take_profit_are_not_late(candles: list[Candle], positions: list[PaperPosition]) -> None:

    for p in positions:
        end = p.exit_time if p.exit_time is not None else candles[-1].start + STEP
        held = [c for c in candles if p.entry_time < c.start < end]
        for candle in held:
            # 보유 중 어떤 마감에서도 손절·익절 기준에 닿았다면 그 캔들에서 팔았어야 한다
            rate = _rate(candle.close, p.average_price)
            assert -p.stop_loss_rate < rate < p.take_profit_rate, (p, candle.start, rate)


def _check_entry_stochastic_below_entry_max(candles: list[Candle], positions: list[PaperPosition]) -> None:

    for p in positions:
        index = next(i for i, c in enumerate(candles) if c.start == p.entry_time)
        window = candles[max(index + 1 - WINDOW, 0) : index + 1]
        stochastic = calculate(window, IndicatorSettings()).stochastic
        k, d = stochastic.main[-1], stochastic.signal[-1]
        # 지표를 따로 계산해 매수 시점의 %K, %D가 기준 이하였는지 확인한다 (매매 로직 5.6)
        assert k is not None and d is not None
        assert k <= ENTRY_MAX and d <= ENTRY_MAX


async def test_paper_simulation_follows_trading_rules(database: Database) -> None:
    """캔들 마감 400번을 실제 MarketWorker와 DB로 처리하고 결과를 검사한다. 끝나면 데이터를 지운다."""
    symbol = "KRW-T" + uuid.uuid4().hex[:8].upper()
    async with database.transaction() as conn:
        market = await MarketRepository().add(conn, symbol)
    assert market is not None
    candles = _walk()
    worker = MarketWorker(
        upbit=HistoricalUpbit(candles),
        database=database,
        timeframe=Timeframe.MINUTE_30,
        indicator_settings=IndicatorSettings(),
        signal_settings=SignalSettings(),
        exit_settings=EXIT,
        stochastic_entry_max=ENTRY_MAX,
        market_ids={symbol: market.market_id},
    )
    try:
        for end in range(WINDOW, CANDLES + 1):
            await worker.candle_close_job(symbol, START + STEP * end)()
        positions = await _positions(database, market.market_id)

        _check_simulation_covers_every_exit_reason(candles, positions)
        _check_trades_match_in_memory_reference(candles, positions)
        _check_positions_do_not_overlap(candles, positions)
        _check_prices_and_profit_follow_candles(candles, positions)
        _check_exit_reason_matches_profit_range(candles, positions)
        _check_stop_loss_and_take_profit_are_not_late(candles, positions)
        _check_entry_stochastic_below_entry_max(candles, positions)

        # 마지막 마감을 한 번 더 처리해도 아무것도 바뀌지 않는다 (같은 마켓을 두 번 사거나 팔지 않음)
        await worker.candle_close_job(symbol, START + STEP * CANDLES)()
        assert await _positions(database, market.market_id) == positions
        async with database.transaction() as conn:
            times = await conn.fetch(
                "SELECT DISTINCT candle_time FROM indicator_info WHERE market_id = $1", market.market_id
            )
        assert [row["candle_time"] for row in times] == [START + STEP * (CANDLES - 1)]
    finally:
        async with database.transaction() as conn:
            await conn.execute("DELETE FROM paper_position WHERE market_id = $1", market.market_id)
            await conn.execute("DELETE FROM indicator_info WHERE market_id = $1", market.market_id)
            await conn.execute("DELETE FROM market WHERE market_id = $1", market.market_id)


async def _positions(database: Database, market_id: int) -> list[PaperPosition]:
    async with database.transaction() as conn:
        rows = await conn.fetch("SELECT * FROM paper_position WHERE market_id = $1 ORDER BY entry_time", market_id)
    return [PaperPosition.from_record(row) for row in rows]
