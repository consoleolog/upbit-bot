"""매매 중단 해제. 사람이 중단 원인을 확인하고 바로잡은 뒤에만 쓴다.

사용법 (서버): sudo docker compose exec bot python -m src.resume_market KRW-BTC
해제는 DB의 중단 기록만 지운다. 실행 중인 봇은 시작할 때 읽은 중단을 계속 지키므로,
해제한 뒤 봇을 재시작해야 그 마켓의 매매가 다시 시작된다: sudo docker compose restart bot
끝나지 않은 주문이 남아 있으면 재시작한 뒤 다시 확인하고, 여전히 맞지 않으면 다시 중단된다.
DB 접속 정보는 DB_* 환경 변수에서 읽는다. DISCORD_WEBHOOK_URL이 있으면 해제를 알린다.
"""

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence

from src.app.notifier import DiscordNotifier
from src.connections.database import Database, DatabaseConfig
from src.exchange.candle import KST
from src.repositories.errors import NotFoundError
from src.repositories.market import MarketRepository
from src.repositories.market_halt import MarketHaltRepository
from src.repositories.models import MarketHalt

RESTART_COMMAND = "sudo docker compose restart bot"


async def resume(config: DatabaseConfig, symbol: str) -> MarketHalt:
    """마켓의 중단 기록을 지우고 지운 기록을 돌려준다. 마켓이 없거나 중단돼 있지 않으면 NotFoundError."""
    database = Database(config)
    await database.connect()
    try:
        async with database.transaction() as conn:
            market = await MarketRepository().get_by_symbol(conn, symbol)
            return await MarketHaltRepository().remove(conn, market.market_id)
    finally:
        await database.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="resume-market", description="매매 중단 해제 (원인을 확인한 뒤에만)")
    parser.add_argument("market", help="해제할 마켓 코드 (예: KRW-BTC)")
    args = parser.parse_args(argv)
    try:
        config = DatabaseConfig.from_env("DB_")
    except ValueError as error:
        print(f"설정 오류: {error}", file=sys.stderr)
        return 1
    try:
        halt = asyncio.run(resume(config, args.market))
    except NotFoundError:
        print(f"{args.market}: 중단된 마켓이 아닙니다 (마켓이 없거나 중단 기록이 없음)", file=sys.stderr)
        return 1
    since = halt.halted_at.astimezone(KST).strftime("%m-%d %H:%M")
    print(f"{args.market}: 중단 해제 ({since}부터 중단, 사유: {halt.reason})")
    print(f"봇을 재시작해야 매매가 다시 시작됩니다: {RESTART_COMMAND}")
    _notify(f"✅ [중단 해제] {args.market}\n{since}부터 중단 · {halt.reason}\n봇을 재시작하면 매매를 다시 시작합니다.")
    return 0


def _notify(text: str) -> None:
    """해제를 디스코드로 알린다. 알리지 못해도 해제는 이미 끝났으므로 실패로 보지 않는다."""
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        return
    try:
        notifier = DiscordNotifier(url)
    except ValueError as error:
        # 메시지에 URL은 들어가지 않는다
        print(f"알림 설정 오류로 알리지 못했습니다: {error}", file=sys.stderr)
        return
    # 보내기 실패(시간 초과, 거부)는 notifier가 로그로 남기고 예외를 내지 않는다
    asyncio.run(notifier.send_now(text))


if __name__ == "__main__":
    sys.exit(main())
