"""매매 결과 보기. DB를 조회만 하고 아무것도 바꾸지 않는다.

사용법 (서버): sudo docker compose exec bot python -m src.show_results [--paper] [--limit N]
기본은 실거래(열린 포지션, 최근 닫힌 포지션, 최근 주문)이고, --paper면 모의 포지션을 보여 준다.
DB 접속 정보는 DB_* 환경 변수에서 읽는다. 시각은 한국 시간으로 보여 준다.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

import asyncpg

from src.connections.database import Database, DatabaseConfig
from src.exchange.candle import KST

DEFAULT_LIMIT = 10

_OPEN_POSITIONS = """
    SELECT m.symbol, p.entry_volume, p.entry_cost, p.sold_volume, p.opened_at
    FROM position p JOIN market m USING (market_id)
    WHERE p.status = 'OPEN'
    ORDER BY p.opened_at
"""
_CLOSED_POSITIONS = """
    SELECT m.symbol, p.entry_cost, p.sold_proceeds, p.exit_reason, p.opened_at, p.closed_at
    FROM position p JOIN market m USING (market_id)
    WHERE p.status = 'CLOSED'
    ORDER BY p.closed_at DESC
    LIMIT $1
"""
_CLOSED_TOTAL = """
    SELECT count(*) AS trades, count(*) FILTER (WHERE sold_proceeds > entry_cost) AS wins,
           coalesce(sum(sold_proceeds - entry_cost), 0) AS profit
    FROM position
    WHERE status = 'CLOSED'
"""
_RECENT_ORDERS = """
    SELECT m.symbol, o.side, o.reason, o.state, o.requested_amount, o.requested_volume,
           o.executed_volume, o.executed_funds, o.paid_fee, o.created_at
    FROM orders o JOIN market m USING (market_id)
    ORDER BY o.created_at DESC
    LIMIT $1
"""
_PAPER_POSITIONS = """
    SELECT m.symbol, p.status, p.entry_time, p.average_price, p.exit_time, p.exit_reason, p.net_profit_rate
    FROM paper_position p JOIN market m USING (market_id)
    ORDER BY p.entry_time DESC
    LIMIT $1
"""
_PAPER_TOTAL = """
    SELECT count(*) AS trades, count(*) FILTER (WHERE net_profit_rate > 0) AS wins,
           coalesce(sum(net_profit_rate), 0) AS rate_sum
    FROM paper_position
    WHERE status = 'CLOSED'
"""


async def live_report(conn: asyncpg.Connection, limit: int) -> list[str]:
    """실거래 결과. 수익률은 매수·매도 수수료를 모두 뺀 값이다."""
    lines = ["== 열린 포지션"]
    for row in await conn.fetch(_OPEN_POSITIONS):
        holding = row["entry_volume"] - row["sold_volume"]
        average = row["entry_cost"] / row["entry_volume"]
        lines.append(f"  {row['symbol']}  보유 {holding}  평단가 {_won(average)}  매수 {_time(row['opened_at'])}")
    if len(lines) == 1:
        lines.append("  없음")

    total = await conn.fetchrow(_CLOSED_TOTAL)
    lines.append(
        f"== 닫힌 포지션 (전체 {total['trades']}건, 수익 {total['wins']}건, 손익 합계 {_won(total['profit'])}원)"
    )
    for row in await conn.fetch(_CLOSED_POSITIONS, limit):
        profit = row["sold_proceeds"] - row["entry_cost"]
        lines.append(
            f"  {row['symbol']}  {row['exit_reason']}  {_percent(profit / row['entry_cost'] * 100)}  "
            f"손익 {_won(profit)}원  {_time(row['opened_at'])} → {_time(row['closed_at'])}"
        )

    lines.append(f"== 최근 주문 {limit}건")
    for row in await conn.fetch(_RECENT_ORDERS, limit):
        requested = f"{_won(row['requested_amount'])}원" if row["side"] == "bid" else f"{row['requested_volume']}개"
        lines.append(
            f"  {_time(row['created_at'])}  {row['symbol']}  {row['side']}  {row['reason']}  {row['state']}  "
            f"주문 {requested}  체결 {row['executed_volume']}개 {_won(row['executed_funds'])}원  "
            f"수수료 {row['paid_fee']}"
        )
    return lines


async def paper_report(conn: asyncpg.Connection, limit: int) -> list[str]:
    """모의 실행 결과. 수익률은 판단한 가격으로 체결됐다고 보고 수수료를 뺀 값이다."""
    total = await conn.fetchrow(_PAPER_TOTAL)
    lines = [
        f"== 모의 포지션 (닫힌 것 {total['trades']}건, 수익 {total['wins']}건, "
        f"수익률 합계 {_percent(total['rate_sum'])})"
    ]
    for row in await conn.fetch(_PAPER_POSITIONS, limit):
        if row["status"] == "OPEN":
            lines.append(
                f"  {row['symbol']}  보유 중  평단가 {_won(row['average_price'])}  매수 {_time(row['entry_time'])}"
            )
        else:
            lines.append(
                f"  {row['symbol']}  {row['exit_reason']}  {_percent(row['net_profit_rate'])}  "
                f"{_time(row['entry_time'])} → {_time(row['exit_time'])}"
            )
    return lines


def _won(value: Decimal) -> str:
    return f"{value:,.0f}"


def _percent(value: Decimal) -> str:
    return f"{value:+.3f}%"


def _time(value: datetime) -> str:
    return value.astimezone(KST).strftime("%m-%d %H:%M")


async def show(config: DatabaseConfig, *, paper: bool, limit: int) -> list[str]:
    database = Database(config)
    await database.connect()
    try:
        async with database.transaction() as conn:
            # 조회만 한다. 실수로 바꾸는 문장이 들어가도 DB가 거부한다
            await conn.execute("SET TRANSACTION READ ONLY")
            return await (paper_report(conn, limit) if paper else live_report(conn, limit))
    finally:
        await database.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="show-results", description="매매 결과 보기 (조회만)")
    parser.add_argument("--paper", action="store_true", help="모의 실행 결과를 본다")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"최근 몇 건을 볼지 (기본 {DEFAULT_LIMIT})")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    try:
        config = DatabaseConfig.from_env("DB_")
    except ValueError as error:
        print(f"설정 오류: {error}", file=sys.stderr)
        return 1
    for line in asyncio.run(show(config, paper=args.paper, limit=args.limit)):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
