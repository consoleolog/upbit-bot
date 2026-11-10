"""실거래 주문 실행. 주문을 기록하고, 업비트에 보내고, 체결을 확인해 포지션에 반영한다 (매매 로직 5.1, 6, 7).

주문은 다시 보내지 않는다. 응답을 받지 못한 주문은 DB에 끝나지 않은 주문으로 남고, 그 마켓은 identifier로
거래소에서 결과를 확인할 때까지 새 주문을 내지 않는다. 거래소 호출은 트랜잭션 밖에서 한다.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

import asyncpg

from src.app.config import ExitSettings
from src.connections.database import Database
from src.exchange.order import Order, OrderSide, OrderState
from src.exchange.upbit_client import UpbitError, UpbitUnknownResultError
from src.repositories.indicator_info import IndicatorInfoRepository
from src.repositories.models import LiveOrder, LiveOrderState
from src.repositories.order import OrderRepository
from src.repositories.position import PositionRepository
from src.trading.reasons import Reason

logger = logging.getLogger(__name__)

# 주문을 보낸 뒤 체결을 확인하는 횟수와 간격(초). 시장가 주문은 보통 바로 체결된다. 근거 있는 값은 아니다
CONFIRM_ATTEMPTS = 5
CONFIRM_INTERVAL = 1.0
# 응답을 받지 못한 주문을 조회했는데 없다고 나와도, 기록한 지 이만큼 지나기 전에는 접수되지 않았다고 단정하지 않는다.
# 타임아웃 직후에는 거래소에 아직 반영되지 않았을 수 있고, 그때 실패로 보고 새 주문을 내면 중복 주문이 된다
NOT_FOUND_GRACE = timedelta(seconds=60)


class OrderClient(Protocol):
    async def place_market_buy(self, market: str, krw_amount: Decimal, identifier: str) -> Order: ...

    async def place_market_sell(self, market: str, volume: Decimal, identifier: str) -> Order: ...

    async def get_order(self, identifier: str) -> Order | None: ...


class InconsistentOrderError(Exception):
    """거래소의 주문 결과를 그대로 반영할 수 없음 (체결 합계 불일치, 열린 포지션과 맞지 않음 등)."""


class LiveExecutor:
    """마켓별 실거래 주문을 실행한다. 같은 마켓의 작업은 애플리케이션의 마켓 잠금 안에서 하나씩 실행된다고 가정한다.

    상태를 확신할 수 없는 일이 생기면 그 마켓의 매매를 중단한다(메모리, 재시작 전까지). 사람이 확인해야 한다.
    """

    def __init__(
        self,
        *,
        upbit: OrderClient,
        database: Database,
        exit_settings: ExitSettings,
        ask_min_totals: Mapping[str, Decimal],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._upbit = upbit
        self._database = database
        # 체결된 매수로 포지션을 열 때 남기는 매도 기준 (매매 로직 2.3)
        self._exit_settings = exit_settings
        # 마켓별 최소 매도 금액. 매도하고 남은 수량이 이보다 작으면 더 팔 수 없으므로 포지션을 닫는다
        self._ask_min_totals = ask_min_totals
        self._sleep = sleep
        self._clock = clock
        self._orders = OrderRepository()
        self._positions = PositionRepository()
        self._halted: set[str] = set()

    def is_halted(self, market: str) -> bool:
        return market in self._halted

    async def resolve_unfinished(self, market: str, market_id: int) -> bool:
        """마켓에 끝나지 않은 주문이 있으면 거래소에서 결과를 확인해 반영한다. 있었으면 True.

        True면 이번 작업에서는 매매를 판단하지 않는다 (매매 로직 5.1).
        """
        if self.is_halted(market):
            return True
        async with self._database.transaction() as conn:
            order = await self._orders.get_unfinished(conn, market_id)
        if order is None:
            return False
        await self._confirm(market, order, attempts=1)
        return True

    async def buy(self, market: str, market_id: int, amount: Decimal) -> LiveOrder | None:
        """원화 amount만큼 시장가로 매수한다. 주문하지 않았으면 None (매매 중단, 끝나지 않은 주문이 있음)."""
        return await self._execute(market, market_id, OrderSide.BID, Reason.ENTRY, amount)

    async def sell(self, market: str, market_id: int, volume: Decimal, reason: Reason) -> LiveOrder | None:
        """수량 volume을 시장가로 매도한다. 주문하지 않았으면 None."""
        return await self._execute(market, market_id, OrderSide.ASK, reason, volume)

    async def _execute(
        self, market: str, market_id: int, side: OrderSide, reason: Reason, quantity: Decimal
    ) -> LiveOrder | None:
        if self.is_halted(market):
            logger.warning("order skipped: trading halted (market=%s, side=%s, reason=%s)", market, side, reason)
            return None
        # 보내기 전에 기록하고 커밋한다. 보내는 도중 봇이 멈춰도 identifier로 결과를 확인할 수 있다
        async with self._database.transaction() as conn:
            if side is OrderSide.BID:
                order = await self._orders.create_buy(conn, market_id=market_id, amount=quantity)
            else:
                order = await self._orders.create_sell(conn, market_id=market_id, volume=quantity, reason=reason)
        if order is None:
            logger.warning("order skipped: unfinished order exists (market=%s, side=%s)", market, side)
            return None
        logger.info(
            "order sending (market=%s, side=%s, reason=%s, quantity=%s, identifier=%s)",
            market,
            side,
            reason,
            quantity,
            order.identifier,
        )
        try:
            if side is OrderSide.BID:
                await self._upbit.place_market_buy(market, quantity, order.identifier)
            else:
                await self._upbit.place_market_sell(market, quantity, order.identifier)
        except UpbitUnknownResultError as error:
            # 접수됐는지 알 수 없다. 다시 보내지 않고 identifier로 확인한다
            logger.warning("order result unknown (market=%s, identifier=%s): %s", market, order.identifier, error)
        except UpbitError as error:
            # 거래소가 접수하지 않은 것이 확실하다 (요청 오류, 권한, 요청 수 제한, 연결 실패 등)
            async with self._database.transaction() as conn:
                failed = await self._orders.record_result(
                    conn,
                    order_id=order.order_id,
                    state=LiveOrderState.FAILED,
                    executed_volume=Decimal(0),
                    executed_funds=Decimal(0),
                    paid_fee=Decimal(0),
                )
            # 같은 주문이 매 판단마다 거절되는 것을 막는다. 잔고가 기록과 다른 경우 등은 사람이 확인해야 한다
            self._halt(market, f"order rejected ({error.status} {error.name}): {error}")
            return failed
        return await self._confirm(market, order, attempts=CONFIRM_ATTEMPTS)

    async def _confirm(self, market: str, order: LiveOrder, *, attempts: int) -> LiveOrder:
        """거래소에서 주문 결과를 확인해 반영한다. 끝나지 않았거나 확인하지 못하면 기록된 상태 그대로 돌려준다."""
        for attempt in range(attempts):
            if attempt:
                await self._sleep(CONFIRM_INTERVAL)
            try:
                found = await self._upbit.get_order(order.identifier)
            except UpbitError as error:
                # 조회 실패는 결과를 알 수 없다는 뜻이다. 그대로 두고 다음 작업에서 다시 확인한다
                logger.warning("order check failed (market=%s, identifier=%s): %s", market, order.identifier, error)
                continue
            if found is not None and (found.market, found.side) != (market, order.side):
                self._halt(
                    market, f"exchange order {order.identifier} does not match record: {found.market} {found.side}"
                )
                return order
            if found is None:
                if self._clock() - order.created_at < NOT_FOUND_GRACE:
                    continue
                return await self._record_not_accepted(market, order)
            if found.state in (OrderState.DONE, OrderState.CANCEL):
                return await self._apply(market, order, found)
            if order.state is LiveOrderState.PENDING:
                # 접수는 확인됐다. 체결이 끝나지 않았으면 다음 작업에서 다시 확인한다
                order = await self._record_waiting(order, found)
        logger.warning(
            "order not finished yet (market=%s, identifier=%s, state=%s)", market, order.identifier, order.state
        )
        return order

    async def _record_not_accepted(self, market: str, order: LiveOrder) -> LiveOrder:
        async with self._database.transaction() as conn:
            failed = await self._orders.record_result(
                conn,
                order_id=order.order_id,
                state=LiveOrderState.FAILED,
                executed_volume=Decimal(0),
                executed_funds=Decimal(0),
                paid_fee=Decimal(0),
            )
        logger.warning(
            "order not found on exchange; recorded as failed (market=%s, identifier=%s)", market, order.identifier
        )
        return failed

    async def _record_waiting(self, order: LiveOrder, found: Order) -> LiveOrder:
        async with self._database.transaction() as conn:
            return await self._orders.record_result(
                conn,
                order_id=order.order_id,
                state=LiveOrderState.WAIT,
                executed_volume=Decimal(0),
                executed_funds=Decimal(0),
                paid_fee=Decimal(0),
            )

    async def _apply(self, market: str, order: LiveOrder, found: Order) -> LiveOrder:
        """끝난 주문의 체결을 주문·포지션에 반영하고, 체결이 있으면 신호 상태를 비운다 (매매 로직 7). 한 트랜잭션이다."""
        try:
            funds = _executed_funds(found)
            async with self._database.transaction() as conn:
                finished = await self._orders.record_result(
                    conn,
                    order_id=order.order_id,
                    state=LiveOrderState(found.state),
                    executed_volume=found.executed_volume,
                    executed_funds=funds,
                    paid_fee=found.paid_fee,
                )
                if found.executed_volume > 0:
                    await self._apply_fill(conn, market, finished, found)
                    await IndicatorInfoRepository().clear_signal_states(conn, order.market_id)
        # 거래소 결과가 기록과 맞지 않거나(체결 합계 불일치 등) DB 제약·값 검사에 걸리면 반영할 수 없다
        except (InconsistentOrderError, asyncpg.IntegrityConstraintViolationError, ValueError) as error:
            # 반영하지 않았으므로 주문은 끝나지 않은 상태로 남고, 이 마켓은 새 주문을 내지 않는다
            self._halt(market, f"cannot apply order {order.identifier}: {error}")
            return order
        logger.info(
            "order finished (market=%s, side=%s, reason=%s, state=%s, volume=%s, funds=%s, fee=%s)",
            market,
            finished.side,
            finished.reason,
            finished.state,
            finished.executed_volume,
            finished.executed_funds,
            finished.paid_fee,
        )
        return finished

    async def _apply_fill(self, conn: asyncpg.Connection, market: str, order: LiveOrder, found: Order) -> None:
        filled_at = max(trade.created_at for trade in found.trades)
        if order.side is OrderSide.BID:
            # 평단가는 매수 수수료를 포함한다 (매매 로직 5.2)
            opened = await self._positions.open(
                conn,
                market_id=order.market_id,
                volume=order.executed_volume,
                cost=order.executed_funds + order.paid_fee,
                opened_at=filled_at,
                exit_settings=self._exit_settings,
                first_buy_amount=order.requested_amount,
            )
            if opened is None:
                raise InconsistentOrderError("buy filled while a position is already open")
            return
        position = await self._positions.get_open(conn, order.market_id)
        if position is None:
            raise InconsistentOrderError("sell filled without an open position")
        if order.executed_volume > position.holding_volume:
            raise InconsistentOrderError(f"sold {order.executed_volume} more than holding {position.holding_volume}")
        position = await self._positions.add_sell(
            conn,
            position_id=position.position_id,
            volume=order.executed_volume,
            proceeds=order.executed_funds - order.paid_fee,
        )
        # 남은 수량을 마지막 체결가로 팔아도 최소 주문 금액에 못 미치면 더 팔 수 없으므로 닫는다
        last_price = max(found.trades, key=lambda trade: trade.created_at).price
        if position.holding_volume * last_price < self._ask_min_totals[market]:
            if position.holding_volume > 0:
                logger.warning(
                    "closing position with unsellable remainder (market=%s, volume=%s)", market, position.holding_volume
                )
            await self._positions.close(
                conn, position_id=position.position_id, closed_at=filled_at, exit_reason=order.reason
            )

    def _halt(self, market: str, why: str) -> None:
        self._halted.add(market)
        logger.error("trading halted for %s until restart: %s", market, why)


def _executed_funds(order: Order) -> Decimal:
    """체결 금액 합계(수수료 제외). 체결 목록이 체결 수량과 맞지 않으면 InconsistentOrderError."""
    volume = sum((trade.volume for trade in order.trades), Decimal(0))
    if volume != order.executed_volume:
        raise InconsistentOrderError(f"trades volume {volume} != executed volume {order.executed_volume}")
    return sum((trade.funds for trade in order.trades), Decimal(0))
