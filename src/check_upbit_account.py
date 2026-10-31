"""업비트 API 키 확인. 계좌와 설정한 마켓의 주문 가능 정보를 조회해 보여 준다. 실제 주문은 하지 않는다.

사용법: python -m src.check_upbit_account --config config/trading.json [--order-test]
(UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY 환경 변수 필요. 키나 토큰은 출력하지 않는다)

--order-test를 주면 마켓마다 최소 주문 금액의 시장가 매수를 업비트 주문 생성 테스트로 검증한다.
주문은 만들어지지 않지만 키에 주문하기 권한이 있어야 하고, 원화 잔고가 최소 주문 금액보다 적으면 거절된다.
"""

import argparse
import asyncio
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path

from src.app.config import ConfigError, load_config
from src.exchange.upbit_auth import UpbitCredentials
from src.exchange.upbit_client import UpbitClient, UpbitError


async def check(credentials: UpbitCredentials, markets: Sequence[str], *, order_test: bool = False) -> None:
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
            if order_test:
                # 주문 생성 테스트만 쓴다. 실제 주문 함수(place_*)는 부르지 않는다
                order = await client.test_market_buy(market, chance.bid_min_total, f"order-test-{uuid.uuid4().hex}")
                print(
                    f"  {market}: 주문 생성 테스트 통과 (실제 주문 없음) - 시장가 매수 {order.price}원, 상태 {order.state}"
                )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check-upbit-account", description="업비트 API 키 확인 (실제 주문 없음)")
    parser.add_argument("--config", type=Path, required=True, help="설정 파일 경로")
    parser.add_argument(
        "--order-test",
        action="store_true",
        help="주문 생성 테스트로 시장가 매수를 검증 (실제 주문 없음, 주문하기 권한 필요)",
    )
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        credentials = UpbitCredentials.from_env()
    except (ConfigError, ValueError) as error:
        print(f"설정 오류: {error}", file=sys.stderr)
        return 1
    try:
        asyncio.run(check(credentials, config.markets, order_test=args.order_test))
    except UpbitError as error:
        # 401·403이면 키, 허용 IP, 권한(자산조회, --order-test는 주문하기)을 확인한다
        print(f"업비트 요청 실패: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
