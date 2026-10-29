"""모의 실행. 매매 판단을 실제 주문 없이 모의 포지션으로 기록한다."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

import asyncpg

from src.app.config import ExitSettings
from src.exchange.candle import Candle
from src.repositories.indicator_info import IndicatorInfoRepository
from src.repositories.models import IndicatorInfo, IndicatorType, PaperPosition
from src.repositories.paper_position import PaperPositionRepository
from src.trading.decision import Action, Decision, Holding, decide, net_profit_rate

# 업비트 KRW 마켓 수수료(0.05%). 실제 주문을 만들 때 거래소에서 받은 값으로 바꾼다
PAPER_FEE_RATE = Decimal("0.0005")


@dataclass(frozen=True, kw_only=True)
class PaperTrade:
    decision: Decision
    position: PaperPosition


async def apply_paper_decision(
    conn: asyncpg.Connection,
    *,
    market_id: int,
    candle: Candle,
    states: Mapping[IndicatorType, IndicatorInfo],
    exit_settings: ExitSettings,
    stochastic_entry_max: float,
    allow_buy: bool,
) -> PaperTrade | None:
    """candle(방금 마감된 캔들)에서 매매를 판단해 모의 매수·매도를 기록한다. 매매하지 않으면 None.

    매수·매도를 기록하면 이 마켓의 신호 상태를 모두 비운다 (매매 로직 7).
    allow_buy가 False면(계산 구간이 짧은 캔들, 매매 로직 3.0) 매수하지 않는다. 손절·익절·신호 매도는 판단한다.
    exit_settings는 새로 매수할 포지션의 매도 기준이다. 보유 중인 포지션은 매수할 때 저장한 기준을 쓴다 (매매 로직 2.3).
    호출하는 쪽의 트랜잭션 안에서 실행해, 신호 상태 저장과 함께 커밋되게 한다.
    """
    positions = PaperPositionRepository()
    current = await positions.get_open(conn, market_id)
    holding = None if current is None else Holding(average_price=current.average_price)
    held_exit_settings = (
        exit_settings
        if current is None
        else ExitSettings(
            take_profit_rate=current.take_profit_rate,
            stop_loss_rate=current.stop_loss_rate,
            min_profit_rate=current.min_profit_rate,
        )
    )
    decision = decide(
        states=states,
        holding=holding,
        candle_time=candle.start,
        close=candle.close,
        exit_settings=held_exit_settings,
        stochastic_entry_max=stochastic_entry_max,
        sell_fee_rate=PAPER_FEE_RATE,
    )
    if decision is None:
        return None
    if decision.action is Action.BUY:
        if not allow_buy:
            return None
        opened = await positions.open(
            conn,
            market_id=market_id,
            entry_time=candle.start,
            entry_price=candle.close,
            average_price=candle.close * (1 + PAPER_FEE_RATE),
            exit_settings=exit_settings,
        )
        # 열린 포지션이 이미 있으면 DB 제약이 막는다. 같은 마켓을 두 번 사지 않는다
        if opened is None:
            return None
        position = opened
    else:
        if current is None:
            # decide는 보유 중일 때만 매도를 돌려준다. 여기에 오면 판단 로직의 버그다
            raise RuntimeError(f"sell decided without an open paper position (market_id={market_id})")
        position = await positions.close(
            conn,
            position_id=current.position_id,
            exit_time=candle.start,
            exit_price=candle.close,
            exit_reason=decision.reason,
            net_profit_rate=net_profit_rate(
                price=candle.close, average_price=current.average_price, sell_fee_rate=PAPER_FEE_RATE
            ),
        )
    await IndicatorInfoRepository().clear_signal_states(conn, market_id)
    return PaperTrade(decision=decision, position=position)
