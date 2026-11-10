"""실거래 판단. 캔들 마감과 실시간 현재가로 매매를 판단해 실거래 주문 실행기에 넘긴다 (매매 로직 5, 6).

거래소 호출이 있으므로 트랜잭션 밖에서 실행한다. 지표·신호 저장은 호출하는 쪽이 먼저 커밋한다.
"""

import asyncio
import logging
from collections.abc import Mapping
from decimal import ROUND_DOWN, Decimal
from typing import Protocol

from src.app.config import ExitSettings, OrderSettings
from src.connections.database import Database
from src.exchange.account import Account, OrderChance
from src.exchange.candle import Candle
from src.exchange.ticker import Ticker
from src.exchange.upbit_client import UpbitError
from src.repositories.models import IndicatorInfo, IndicatorType, LivePosition
from src.repositories.position import PositionRepository
from src.trading.decision import Action, Decision, Holding, decide
from src.trading.live import LiveExecutor
from src.trading.reasons import Reason

logger = logging.getLogger(__name__)

# 원화 잔고를 나타내는 업비트 계좌 화폐 코드
KRW = "KRW"
# 매수 금액은 거래소 최소 주문 금액의 이 배수 이상이어야 한다 (매매 로직 6.1).
# 매도도 최소 주문 금액보다 작으면 거래소가 거절하므로, 가격이 떨어져도 손절 매도를 낼 수 있게 여유를 둔다.
# 2배면 가격이 절반 아래로 떨어지기 전까지는 전량 매도가 최소 금액에 걸리지 않는다
MIN_BUY_MULTIPLE = Decimal(2)


class AccountClient(Protocol):
    async def get_accounts(self) -> list[Account]: ...


class LiveTrader:
    """마켓별 실거래 판단. 같은 마켓의 작업은 애플리케이션의 마켓 잠금 안에서 하나씩 실행된다고 가정한다."""

    def __init__(
        self,
        *,
        executor: LiveExecutor,
        upbit: AccountClient,
        database: Database,
        chances: Mapping[str, OrderChance],
        order_settings: OrderSettings,
        exit_settings: ExitSettings,
        stochastic_entry_max: float,
    ) -> None:
        self._executor = executor
        self._upbit = upbit
        self._database = database
        # 마켓별 수수료율과 최소·최대 주문 금액 (시작할 때 거래소에서 받은 값, 매매 로직 5.2, 6.1)
        self._chances = chances
        # 매수 비율과 추가 매수 배수·횟수 한도 (매매 로직 5.6, 6.1)
        self._order = order_settings
        # 새로 매수할 포지션의 매도 기준. 보유 중인 포지션은 매수할 때 저장한 기준을 쓴다 (매매 로직 2.3)
        self._exit_settings = exit_settings
        self._stochastic_entry_max = stochastic_entry_max
        self._positions = PositionRepository()
        # 여러 마켓의 매수를 하나씩 처리한다. 같은 잔고를 함께 보고 계산하면 잔고보다 많이 사려 할 수 있다 (매매 로직 6.1)
        self._buy_lock = asyncio.Lock()

    async def on_candle(
        self,
        market: str,
        market_id: int,
        *,
        candle: Candle,
        states: Mapping[IndicatorType, IndicatorInfo],
        allow_buy: bool,
    ) -> None:
        """방금 마감된 candle에서 매매를 판단해 주문한다. allow_buy가 False면 매수하지 않는다 (매매 로직 3.0)."""
        if await self._blocked(market, market_id):
            return
        position = await self._open_position(market_id)
        decision = decide(
            states=states,
            holding=None if position is None else Holding(average_price=position.average_price),
            candle_time=candle.start,
            close=candle.close,
            exit_settings=self._exit_settings if position is None else _held_exit_settings(position),
            stochastic_entry_max=self._stochastic_entry_max,
            sell_fee_rate=self._chances[market].ask_fee,
            can_add=position is not None and position.buy_count - 1 < self._order.max_add_buys,
        )
        if decision is None:
            return
        if decision.action is Action.BUY:
            if not allow_buy:
                return
            if decision.reason is Reason.ADD:
                if position is None:
                    # decide는 보유 중일 때만 추가 매수를 돌려준다. 여기에 오면 판단 로직의 버그다
                    raise RuntimeError(f"add buy decided without an open position (market={market})")
                await self._add_buy(market, market_id, position)
            else:
                await self._buy(market, market_id)
            return
        await self._sell(market, market_id, position, decision)

    async def on_ticker(self, market: str, market_id: int, ticker: Ticker) -> None:
        """캔들 마감 사이에 현재가로 손절·익절만 판단한다 (매매 로직 5.1)."""
        if await self._blocked(market, market_id):
            return
        position = await self._open_position(market_id)
        # 매수 체결보다 앞선 체결가로는 판단하지 않는다
        if position is None or ticker.traded_at <= position.opened_at:
            return
        decision = decide(
            states={},
            holding=Holding(average_price=position.average_price),
            candle_time=ticker.traded_at,
            close=ticker.price,
            exit_settings=_held_exit_settings(position),
            # 보유 중이라 매수 판단은 하지 않으므로 쓰이지 않는다
            stochastic_entry_max=0.0,
            sell_fee_rate=self._chances[market].ask_fee,
        )
        if decision is not None:
            await self._sell(market, market_id, position, decision)

    async def _blocked(self, market: str, market_id: int) -> bool:
        """매매가 중단됐거나 끝나지 않은 주문이 있으면 True. 그때는 판단하지 않는다 (매매 로직 5.1).

        끝나지 않은 주문은 여기서 거래소에 조회해 결과를 반영한다.
        """
        if self._executor.is_halted(market):
            return True
        return await self._executor.resolve_unfinished(market, market_id)

    async def _open_position(self, market_id: int) -> LivePosition | None:
        async with self._database.transaction() as conn:
            return await self._positions.get_open(conn, market_id)

    async def _buy(self, market: str, market_id: int) -> None:
        async with self._buy_lock:
            try:
                accounts = await self._upbit.get_accounts()
            except UpbitError as error:
                # 잔고를 모르면 금액을 정할 수 없으므로 사지 않는다. 신호 상태는 그대로 두어 다음 마감에 다시 판단한다
                logger.warning("buy skipped: balance unavailable (market=%s): %s", market, error)
                return
            balance = next((account.balance for account in accounts if account.currency == KRW), Decimal(0))
            chance = self._chances[market]
            amount = buy_amount(balance=balance, buy_ratio=self._order.buy_ratio, max_total=chance.max_total)
            minimum = min_buy_amount(chance)
            if amount < minimum:
                logger.info(
                    "buy skipped: amount below minimum (market=%s, amount=%s, minimum=%s)", market, amount, minimum
                )
                return
            await self._executor.buy(market, market_id, amount)

    async def _add_buy(self, market: str, market_id: int, position: LivePosition) -> None:
        """첫 매수 주문 금액 * add_buy_multiple만큼 추가 매수한다 (매매 로직 5.6, 6.1).

        잔고가 그 금액(수수료 포함)보다 적거나, 최소·최대 주문 금액을 벗어나면 사지 않는다. 금액을 줄여서 사지 않는다.
        """
        async with self._buy_lock:
            chance = self._chances[market]
            amount = (position.first_buy_amount * self._order.add_buy_multiple).to_integral_value(rounding=ROUND_DOWN)
            minimum = min_buy_amount(chance)
            if not minimum <= amount <= chance.max_total:
                logger.info(
                    "add buy skipped: amount out of range (market=%s, amount=%s, minimum=%s, maximum=%s)",
                    market,
                    amount,
                    minimum,
                    chance.max_total,
                )
                return
            try:
                accounts = await self._upbit.get_accounts()
            except UpbitError as error:
                logger.warning("add buy skipped: balance unavailable (market=%s): %s", market, error)
                return
            balance = next((account.balance for account in accounts if account.currency == KRW), Decimal(0))
            if balance < amount * (1 + chance.bid_fee):
                logger.info(
                    "add buy skipped: balance too low (market=%s, amount=%s, balance=%s)", market, amount, balance
                )
                return
            await self._executor.buy(market, market_id, amount, reason=Reason.ADD)

    async def _sell(self, market: str, market_id: int, position: LivePosition | None, decision: Decision) -> None:
        if position is None:
            # decide는 보유 중일 때만 매도를 돌려준다. 여기에 오면 판단 로직의 버그다
            raise RuntimeError(f"sell decided without an open position (market={market})")
        # 보유 수량 전량을 시장가로 판다 (매매 로직 6.2)
        await self._executor.sell(market, market_id, position.holding_volume, decision.reason)


def buy_amount(*, balance: Decimal, buy_ratio: Decimal, max_total: Decimal) -> Decimal:
    """매수 금액 = 원화 주문 가능 잔고 * buy_ratio / 100, 원 단위로 내림 (매매 로직 6.1). 거래소 최대 주문 금액을 넘지 않는다."""
    amount = (balance * buy_ratio / 100).to_integral_value(rounding=ROUND_DOWN)
    return min(amount, max_total)


def min_buy_amount(chance: OrderChance) -> Decimal:
    """매수할 수 있는 최소 금액. 매수·매도 최소 주문 금액 중 큰 값의 MIN_BUY_MULTIPLE배다."""
    return max(chance.bid_min_total, chance.ask_min_total) * MIN_BUY_MULTIPLE


def _held_exit_settings(position: LivePosition) -> ExitSettings:
    """매수할 때 저장한 매도 기준 (매매 로직 2.3)."""
    return ExitSettings(
        take_profit_rate=position.take_profit_rate,
        stop_loss_rate=position.stop_loss_rate,
        min_profit_rate=position.min_profit_rate,
    )
