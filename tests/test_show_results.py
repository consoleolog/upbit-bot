import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest
from pytest_mock import MockerFixture

from src.app.config import ExitSettings
from src.connections.database import DatabaseConfig
from src.repositories.market import MarketRepository
from src.repositories.models import LiveOrderState
from src.repositories.order import OrderRepository
from src.repositories.paper_position import PaperPositionRepository
from src.repositories.position import PositionRepository
from src.show_results import live_report, main, paper_report, show
from src.trading.reasons import Reason

T0 = datetime(2026, 11, 1, 0, 30, tzinfo=UTC)  # 한국 시간 11-01 09:30


async def _market(conn: asyncpg.Connection) -> tuple[str, int]:
    symbol = "KRW-T" + uuid.uuid4().hex[:8].upper()
    market = await MarketRepository().add(conn, symbol)
    assert market is not None
    return symbol, market.market_id


def _section(lines: list[str], title: str) -> list[str]:
    """제목으로 시작하는 구역의 줄들 (다음 구역 전까지)."""
    start = next(i for i, line in enumerate(lines) if line.startswith(f"== {title}"))
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("==")), len(lines))
    return lines[start:end]


@pytest.mark.integration
async def test_live_report(conn: asyncpg.Connection) -> None:
    positions, orders = PositionRepository(), OrderRepository()
    held, held_id = await _market(conn)
    sold, sold_id = await _market(conn)
    # 열린 포지션: 0.002개를 수수료 포함 200,100원에 샀고 0.0005개를 팔았다
    opened = await positions.open(
        conn,
        market_id=held_id,
        volume=Decimal("0.002"),
        cost=Decimal("200100"),
        opened_at=T0,
        exit_settings=ExitSettings(),
    )
    assert opened is not None
    await positions.add_sell(conn, position_id=opened.position_id, volume=Decimal("0.0005"), proceeds=Decimal("50000"))
    # 닫힌 포지션: 10,005원에 사서 수수료를 빼고 10,155원을 받았다
    closed = await positions.open(
        conn,
        market_id=sold_id,
        volume=Decimal("0.0001"),
        cost=Decimal("10005"),
        opened_at=T0,
        exit_settings=ExitSettings(),
    )
    assert closed is not None
    await positions.add_sell(conn, position_id=closed.position_id, volume=Decimal("0.0001"), proceeds=Decimal("10155"))
    await positions.close(
        conn, position_id=closed.position_id, closed_at=T0 + timedelta(hours=1), exit_reason=Reason.TAKE_PROFIT
    )
    order = await orders.create_buy(conn, market_id=sold_id, amount=Decimal("10000"))
    assert order is not None
    await orders.record_result(
        conn,
        order_id=order.order_id,
        state=LiveOrderState.DONE,
        executed_volume=Decimal("0.0001"),
        executed_funds=Decimal("10000"),
        paid_fee=Decimal("5"),
    )

    lines = await live_report(conn, limit=1000)

    assert f"  {held}  보유 0.0015  평단가 100,050,000  매수 11-01 09:30" in _section(lines, "열린 포지션")
    closed_lines = _section(lines, "닫힌 포지션")
    assert f"  {sold}  TAKE_PROFIT  +1.499%  손익 150원  11-01 09:30 → 11-01 10:30" in closed_lines
    recent = _section(lines, "최근 주문")
    assert any(
        f"  {sold}  bid  ENTRY  done  주문 10,000원  체결 0.0001개 10,000원  수수료 5" in line for line in recent
    )


@pytest.mark.integration
async def test_paper_report(conn: asyncpg.Connection) -> None:
    repo = PaperPositionRepository()
    symbol, market_id = await _market(conn)
    position = await repo.open(
        conn,
        market_id=market_id,
        entry_time=T0,
        entry_price=Decimal("100"),
        average_price=Decimal("100.05"),
        exit_settings=ExitSettings(),
    )
    assert position is not None
    await repo.close(
        conn,
        position_id=position.position_id,
        exit_time=T0 + timedelta(minutes=30),
        exit_price=Decimal("97"),
        exit_reason=Reason.STOP_LOSS,
        net_profit_rate=Decimal("-3.0966"),
    )

    lines = await paper_report(conn, limit=1000)

    assert f"  {symbol}  STOP_LOSS  -3.097%  11-01 09:30 → 11-01 10:00" in lines


@pytest.mark.integration
async def test_show_reads_without_changing(test_db_config: DatabaseConfig) -> None:
    # 실제로 접속해 조회하고, 조회만 하는 트랜잭션에서 끝난다
    lines = await show(test_db_config, paper=False, limit=1)

    assert lines[0] == "== 열린 포지션"
    assert any(line.startswith("== 최근 주문 1건") for line in lines)


def test_main_without_database_env_fails(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    for key in ("HOST", "PORT", "NAME", "USER", "PASSWORD"):
        monkeypatch.delenv(f"DB_{key}", raising=False)

    assert main([]) == 1
    assert "DB_HOST" in capsys.readouterr().err


@pytest.mark.parametrize("limit", ["0", "1001"])
def test_main_rejects_limit_out_of_range(limit: str) -> None:
    with pytest.raises(SystemExit):
        main(["--limit", limit])


@pytest.mark.integration
async def test_show_cannot_write(test_db_config: DatabaseConfig, mocker: MockerFixture) -> None:
    async def write(conn: asyncpg.Connection, limit: int) -> list[str]:
        await conn.execute("UPDATE market SET is_active = is_active")
        return []

    mocker.patch("src.show_results.live_report", side_effect=write)

    # 조회 명령이 실수로 데이터를 바꾸려 해도 DB가 거부한다
    with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
        await show(test_db_config, paper=False, limit=1)
