"""실시간 손절·익절 (매매 로직 5.1). 현재가가 올 때마다 보유 중인 모의 포지션을 판단한다."""

import logging
from collections.abc import Callable, Mapping

from src.app.application import Job
from src.connections.database import Database
from src.exchange.ticker import Ticker
from src.trading.paper import apply_realtime_exit

logger = logging.getLogger(__name__)


class RealtimeExit:
    """현재가를 받아 마켓별 판단 작업을 애플리케이션에 넘긴다.

    현재가는 초당 여러 번 올 수 있어, 마켓마다 판단 작업은 최대 하나만 기다리게 하고 실행할 때 가장 최근 현재가로 판단한다.
    작업은 마켓 잠금 안에서 실행되므로 같은 마켓의 캔들 마감 작업과 동시에 실행되지 않는다.
    """

    def __init__(
        self, *, database: Database, market_ids: Mapping[str, int], submit: Callable[[str, Job], bool]
    ) -> None:
        self._database = database
        self._market_ids = market_ids
        self._submit = submit
        self._latest: dict[str, Ticker] = {}
        self._pending: set[str] = set()

    def on_ticker(self, ticker: Ticker) -> None:
        """TickerFeed의 콜백. 최근 현재가를 기억하고, 그 마켓의 판단 작업이 기다리는 중이 아니면 새로 넘긴다."""
        market = ticker.market
        self._latest[market] = ticker
        if market in self._pending:
            return
        self._pending.add(market)
        if not self._submit(market, self._job(market)):
            # 애플리케이션이 종료 중이라 받지 않았다
            self._pending.discard(market)

    def _job(self, market: str) -> Job:
        async def job() -> None:
            # 실행을 시작하면 대기 표시를 지운다. 판단하는 동안 온 현재가는 다음 작업으로 판단한다
            self._pending.discard(market)
            await self.check(market, self._latest[market])

        return job

    async def check(self, market: str, ticker: Ticker) -> None:
        async with self._database.transaction() as conn:
            trade = await apply_realtime_exit(conn, market_id=self._market_ids[market], ticker=ticker)
        # 커밋된 뒤에 기록한다
        if trade is not None:
            logger.info(
                "paper %s realtime (market=%s, reason=%s, traded_at=%s, price=%s, average_price=%s, net_profit_rate=%s)",
                trade.decision.action,
                market,
                trade.decision.reason,
                ticker.traded_at.isoformat(),
                ticker.price,
                trade.position.average_price,
                trade.position.net_profit_rate,
            )
