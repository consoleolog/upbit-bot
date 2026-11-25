"""상태 알림. 정해진 시각마다 봇 상태, 보유 포지션, 직전 알림 이후 체결·손익, 매수 신호 진행을 디스코드로 보낸다.

정상이어도 보내므로, 이 알림이 오지 않으면 봇에 문제가 있다는 신호가 된다.
알림은 애플리케이션 작업으로 넘긴다. 만들다 실패해도 작업 실패로 기록될 뿐 매매는 멈추지 않는다.
항목마다 조회에 실패하면 그 줄만 '조회 실패'로 보내고 나머지는 그대로 보낸다.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

import asyncpg

from src.app.application import Job
from src.app.config import StageFilterSettings
from src.app.notifier import Notifier
from src.connections.database import Database
from src.exchange.account import Account
from src.exchange.candle import KST
from src.exchange.retry import retry_read
from src.exchange.ticker import Ticker
from src.exchange.upbit_client import UpbitError
from src.repositories.indicator_info import IndicatorInfoRepository
from src.repositories.order import OrderRepository
from src.repositories.paper_position import PaperPositionRepository
from src.repositories.position import PositionRepository
from src.trading.decision import entry_conditions

logger = logging.getLogger(__name__)

# 보내는 시각 (한국 시간, 4시간마다)
REPORT_HOURS_KST = (1, 5, 9, 13, 17, 21)
# 애플리케이션 작업 잠금 이름. 마켓 작업과 겹치지 않는다
JOB_NAME = "status-report"
KRW = "KRW"
# DB에 접속·조회하지 못한 경우 (연결 끊김, 시간 초과, DB 오류)
DB_ERRORS = (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError)


class BalanceSource(Protocol):
    async def get_accounts(self) -> list[Account]: ...


class HaltSource(Protocol):
    def halted_markets(self) -> list[str]: ...


def next_report_time(now: datetime) -> datetime:
    """now 다음(같은 시각 제외)의 보내는 시각."""
    local = now.astimezone(KST).replace(minute=0, second=0, microsecond=0)
    for hours in range(1, 25):
        candidate = local + timedelta(hours=hours)
        if candidate.hour in REPORT_HOURS_KST and candidate > now:
            return candidate
    raise AssertionError("REPORT_HOURS_KST must not be empty")


class StatusReporter:
    """정해진 시각마다 상태 알림 작업을 넘긴다. 실거래면 balance(업비트 계좌)와 halts(매매 중단)를 준다."""

    def __init__(
        self,
        *,
        database: Database,
        market_ids: Mapping[str, int],
        prices: Callable[[str], Ticker | None],
        notifier: Notifier,
        submit: Callable[[str, Job], bool],
        stochastic_entry_max: float,
        started_at: datetime,
        balance: BalanceSource | None = None,
        halts: HaltSource | None = None,
        stage_filter: StageFilterSettings | None = None,
        stages: Callable[[str], int | None] | None = None,
        clock: Callable[[], datetime],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._database = database
        self._market_ids = market_ids
        self._prices = prices
        self._notifier = notifier
        self._submit = submit
        self._stochastic_entry_max = stochastic_entry_max
        self._balance = balance
        self._halts = halts
        # 스테이지 필터가 있으면 마켓별 마지막 스테이지와 신규 매수가 막혔는지 함께 보낸다
        self._stage_filter = stage_filter
        self._stages = stages
        self._clock = clock
        self._sleep = sleep
        # 직전 알림 시각. 처음에는 봇이 시작한 시각이다
        self._since = started_at
        self._since_label = f"봇 시작({started_at.astimezone(KST):%m-%d %H:%M})"

    @property
    def _live(self) -> bool:
        return self._balance is not None

    async def run(self) -> None:
        """취소될 때까지 보내는 시각마다 알림 작업을 넘긴다. 애플리케이션의 서비스로 실행한다."""
        while True:
            at = next_report_time(self._clock())
            await self._sleep(max((at - self._clock()).total_seconds(), 0.0))
            since, label = self._since, self._since_label
            self._since, self._since_label = at, f"{at.astimezone(KST):%H:%M}"
            self._submit(JOB_NAME, self._job(at, since, label))

    def _job(self, at: datetime, since: datetime, since_label: str) -> Job:
        async def job() -> None:
            self._notifier.notify(await self.build(at, since, since_label))

        return job

    async def build(self, at: datetime, since: datetime, since_label: str) -> str:
        """보낼 메시지. at은 보내는 시각, since부터의 체결·손익을 센다."""
        lines = [f"📊 [상태] {at.astimezone(KST):%m-%d %H:%M} · {'실거래' if self._live else '모의 실행'}"]
        if self._balance is not None:
            lines.append(f"{await self._balance_text(self._balance)} · {self._halt_text()}")
        try:
            lines += await self._db_lines(since, since_label)
        except DB_ERRORS as error:
            # 매매와 상관없는 알림이므로 실패를 알리고 넘어간다
            logger.warning("status report: database query failed (%s)", type(error).__name__)
            lines.append("DB 조회 실패 (보유·체결·신호를 확인하지 못함)")
        return "\n".join(lines)

    async def _balance_text(self, balance: BalanceSource) -> str:
        try:
            accounts = await retry_read(balance.get_accounts, what="status report balance", sleep=self._sleep)
        except UpbitError as error:
            logger.warning("status report: balance unavailable (%s)", type(error).__name__)
            return "원화 조회 실패"
        krw = next((account.balance for account in accounts if account.currency == KRW), Decimal(0))
        return f"원화 {krw:,.0f}원"

    def _halt_text(self) -> str:
        halted = [] if self._halts is None else self._halts.halted_markets()
        return f"🚨 매매 중단: {', '.join(halted)}" if halted else "매매 중단 없음"

    async def _db_lines(self, since: datetime, since_label: str) -> list[str]:
        markets = {market_id: market for market, market_id in self._market_ids.items()}
        async with self._database.transaction() as conn:
            # 조회만 한다
            await conn.execute("SET TRANSACTION READ ONLY")
            if self._live:
                holdings = [
                    (markets[p.market_id], f"{p.holding_volume.normalize():f}개 · ", p.average_price)
                    for p in await PositionRepository().list_open(conn)
                    if p.market_id in markets
                ]
                closed = [
                    p for p in await PositionRepository().list_closed_since(conn, since) if p.market_id in markets
                ]
                filled = await OrderRepository().count_filled_since(conn, since, list(markets))
                profit = sum((p.sold_proceeds - p.entry_cost for p in closed), Decimal(0))
                period = f"체결 {filled}건 · 닫힌 포지션 {len(closed)}건 · 손익 {profit:+,.0f}원"
            else:
                holdings = [
                    (markets[p.market_id], "", p.average_price)
                    for p in await PaperPositionRepository().list_open(conn)
                    if p.market_id in markets
                ]
                paper_closed = [
                    p for p in await PaperPositionRepository().list_closed_since(conn, since) if p.market_id in markets
                ]
                rate = sum((p.net_profit_rate or Decimal(0) for p in paper_closed), Decimal(0))
                period = f"닫힌 포지션 {len(paper_closed)}건 · 수익률 합계 {rate:+.2f}%"
            signals = []
            for market, market_id in self._market_ids.items():
                states = await IndicatorInfoRepository().list_by_market(conn, market_id)
                count = sum(entry_conditions(states, self._stochastic_entry_max))
                signals.append(f"{market} {count}/9{self._stage_text(market)}")
        return [
            "보유" if holdings else "보유 없음",
            *(
                f"  {market} {volume}평단 {average:,.0f} · {self._return_text(market, average)}"
                for market, volume, average in holdings
            ),
            f"{since_label} 이후",
            f"  {period}",
            f"매수 신호 (9개 조건 중{self._stage_legend()})",
            f"  {' · '.join(signals)}",
        ]

    def _stage_legend(self) -> str:
        f = self._stage_filter
        if f is None:
            return ""
        return f", S = EMA {f.ema_short}/{f.ema_mid}/{f.ema_long} 스테이지"

    def _stage_text(self, market: str) -> str:
        """스테이지 필터가 있으면 ' [S2]'처럼 마지막 스테이지를, 신규 매수가 막혔으면 ' [S4 신규 매수 막힘]'을 붙인다."""
        if self._stage_filter is None:
            return ""
        stage = None if self._stages is None else self._stages(market)
        if stage is None:
            # 봇이 시작한 뒤 아직 캔들 마감을 처리하지 않았다
            return " [S?]"
        if not self._stage_filter.allows(stage):
            return f" [S{stage} 신규 매수 막힘]"
        return f" [S{stage}]"

    def _return_text(self, market: str, average: Decimal) -> str:
        ticker = self._prices(market)
        if ticker is None:
            return "현재가 없음"
        return f"현재 {(ticker.price / average - 1) * 100:+.2f}% (수수료 전)"
