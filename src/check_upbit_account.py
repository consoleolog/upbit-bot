"""업비트 API 키 확인. 계좌와 설정한 마켓의 주문 가능 정보를 조회해 보여 준다. 주문은 하지 않는다.

사용법: python -m src.check_upbit_account --config config/trading.json
(UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY 환경 변수 필요. 키나 토큰은 출력하지 않는다)
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from src.app.config import ConfigError, load_config
from src.exchange.upbit_auth import UpbitCredentials
from src.exchange.upbit_client import UpbitClient, UpbitError


async def check(credentials: UpbitCredentials, markets: Sequence[str]) -> None:
    async with UpbitClient(credentials=credentials) as client:
        accounts = await client.get_accounts()
        print("== 잔고")
        for account in accounts:
            print(f"  {account.currency}: 주문 가능 {account.balance}, 주문 중 {account.locked}")
        print("== 주문 가능 정보")
        for market in markets:
            chance = await client.get_order_chance(market)
            print(
                f"  {market}: 매수 수수료 {chance.bid_fee}, 매도 수수료 {chance.ask_fee}, "
                f"최소 주문 {chance.bid_min_total}원, 상태 {chance.state}"
            )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check-upbit-account", description="업비트 API 키 확인 (조회만)")
    parser.add_argument("--config", type=Path, required=True, help="설정 파일 경로")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        credentials = UpbitCredentials.from_env()
    except (ConfigError, ValueError) as error:
        print(f"설정 오류: {error}", file=sys.stderr)
        return 1
    try:
        asyncio.run(check(credentials, config.markets))
    except UpbitError as error:
        # 401이면 키, 허용 IP, 권한(자산조회)을 확인한다
        print(f"업비트 요청 실패: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
