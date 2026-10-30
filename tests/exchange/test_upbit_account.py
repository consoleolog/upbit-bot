"""업비트 계좌·주문 가능 정보 조회. 정상 응답은 업비트 개발자 문서의 응답 예시를 그대로 쓴다."""

import hashlib
from decimal import Decimal
from typing import Any
from urllib.parse import unquote

import httpx
import jwt
import pytest

from src.exchange.account import Account, OrderChance
from src.exchange.rate_limiter import RateLimiter
from src.exchange.upbit_auth import UpbitCredentials
from src.exchange.upbit_client import (
    DEFAULT_GROUP_REQUESTS_PER_SECOND,
    ORDER_GROUP_REQUESTS_PER_SECOND,
    ORDER_TEST_GROUP_REQUESTS_PER_SECOND,
    UpbitAuthError,
    UpbitClient,
    UpbitResponseError,
)

SECRET = "secret-key-1-" + "x" * 64  # HS512 권장 길이(64바이트) 이상
CREDENTIALS = UpbitCredentials(access_key="access-key-1", secret_key=SECRET)

# 업비트 문서 "전체 계좌 조회" 응답 예시
ACCOUNTS = [
    {
        "currency": "KRW",
        "balance": "1000000.0",
        "locked": "0.0",
        "avg_buy_price": "0",
        "avg_buy_price_modified": False,
        "unit_currency": "KRW",
    },
    {
        "currency": "BTC",
        "balance": "2.0",
        "locked": "0.0",
        "avg_buy_price": "140000000",
        "avg_buy_price_modified": False,
        "unit_currency": "KRW",
    },
]

# 업비트 문서 "주문 가능 정보 조회" 응답 예시
CHANCE = {
    "bid_fee": "0.0005",
    "ask_fee": "0.0005",
    "maker_bid_fee": "0.0005",
    "maker_ask_fee": "0.0005",
    "market": {
        "id": "KRW-BTC",
        "name": "BTC/KRW",
        "order_types": ["limit"],
        "order_sides": ["ask", "bid"],
        "bid_types": ["best_fok", "best_ioc", "limit", "limit_fok", "limit_ioc", "price"],
        "ask_types": ["best_fok", "best_ioc", "limit", "limit_fok", "limit_ioc", "market"],
        "bid": {"currency": "KRW", "min_total": "5000"},
        "ask": {"currency": "BTC", "min_total": "5000"},
        "max_total": "1000000000",
        "state": "active",
    },
    "bid_account": {
        "currency": "KRW",
        "balance": "10000",
        "locked": "0",
        "avg_buy_price": "0",
        "avg_buy_price_modified": True,
        "unit_currency": "KRW",
    },
    "ask_account": {
        "currency": "BTC",
        "balance": "0.001",
        "locked": "0",
        "avg_buy_price": "140000000",
        "avg_buy_price_modified": False,
        "unit_currency": "KRW",
    },
}


class Upbit:
    """받은 요청을 기록하고 정해 둔 응답을 돌려주는 가짜 업비트."""

    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.status = status
        self.body = body
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)


async def _call(upbit: Upbit, method: str, *args: object, credentials: UpbitCredentials | None = CREDENTIALS) -> Any:
    async with UpbitClient(transport=httpx.MockTransport(upbit), credentials=credentials) as client:
        return await getattr(client, method)(*args)


def _payload(request: httpx.Request) -> dict[str, str]:
    header = request.headers["Authorization"]
    assert header.startswith("Bearer ")
    return jwt.decode(header.removeprefix("Bearer "), SECRET, algorithms=["HS512"])


async def test_get_accounts() -> None:
    upbit = Upbit(body=ACCOUNTS)

    accounts = await _call(upbit, "get_accounts")

    assert accounts == [
        Account(
            currency="KRW",
            balance=Decimal("1000000.0"),
            locked=Decimal("0.0"),
            avg_buy_price=Decimal("0"),
            unit_currency="KRW",
        ),
        Account(
            currency="BTC",
            balance=Decimal("2.0"),
            locked=Decimal("0.0"),
            avg_buy_price=Decimal("140000000"),
            unit_currency="KRW",
        ),
    ]
    request = upbit.requests[0]
    assert (request.method, request.url.path, request.url.query) == ("GET", "/v1/accounts", b"")
    # 파라미터가 없으면 query_hash를 넣지 않는다
    assert set(_payload(request)) == {"access_key", "nonce"}


async def test_get_order_chance() -> None:
    upbit = Upbit(body=CHANCE)

    chance = await _call(upbit, "get_order_chance", "KRW-BTC")

    assert chance == OrderChance(
        market="KRW-BTC",
        bid_fee=Decimal("0.0005"),
        ask_fee=Decimal("0.0005"),
        bid_min_total=Decimal("5000"),
        ask_min_total=Decimal("5000"),
        max_total=Decimal("1000000000"),
        state="active",
    )


async def test_query_hash_matches_query_actually_sent() -> None:
    upbit = Upbit(body=CHANCE)

    await _call(upbit, "get_order_chance", "KRW-BTC")

    request = upbit.requests[0]
    sent = request.url.query.decode()
    assert sent == "market=KRW-BTC"
    # 토큰의 query_hash가 실제로 보낸 쿼리 문자열(인코딩하지 않은 형태)의 SHA512와 같아야 업비트가 받아들인다
    assert _payload(request)["query_hash"] == hashlib.sha512(unquote(sent).encode()).hexdigest()
    assert _payload(request)["query_hash_alg"] == "SHA512"


async def test_each_request_has_new_nonce() -> None:
    upbit = Upbit(body=ACCOUNTS)
    async with UpbitClient(transport=httpx.MockTransport(upbit), credentials=CREDENTIALS) as client:
        await client.get_accounts()
        await client.get_accounts()

    assert _payload(upbit.requests[0])["nonce"] != _payload(upbit.requests[1])["nonce"]


async def test_without_credentials_does_not_send() -> None:
    upbit = Upbit(body=ACCOUNTS)

    with pytest.raises(RuntimeError, match="credentials are required"):
        await _call(upbit, "get_accounts", credentials=None)

    assert upbit.requests == []


async def test_auth_error_keeps_upbit_error_name_without_secret() -> None:
    # 업비트 문서의 401 예시: 허용 IP가 아닌 곳에서 요청
    upbit = Upbit(401, {"error": {"name": "no_authorization_ip", "message": "This is not a verified IP."}})

    with pytest.raises(UpbitAuthError) as error:
        await _call(upbit, "get_accounts")

    assert error.value.name == "no_authorization_ip"
    assert SECRET not in str(error.value)
    assert "Bearer" not in str(error.value)


def _account(**changes: object) -> list[dict[str, object]]:
    account: dict[str, object] = {**ACCOUNTS[0], **changes}
    return [{key: value for key, value in account.items() if value is not None}]


@pytest.mark.parametrize(
    "body",
    [
        {"currency": "KRW"},
        [None],
        _account(currency=""),
        _account(currency=None),
        _account(balance=1000000.0),
        _account(balance="-1"),
        _account(balance="abc"),
        _account(balance="NaN"),
        _account(locked="Infinity"),
        _account(avg_buy_price=None),
        _account(unit_currency=None),
        _account(unit_currency=1),
        [ACCOUNTS[0], ACCOUNTS[0]],
    ],
)
async def test_invalid_accounts_rejected(body: object) -> None:
    with pytest.raises(UpbitResponseError):
        await _call(Upbit(body=body), "get_accounts")


def _chance(**changes: object) -> dict[str, object]:
    market = {**CHANCE["market"]}  # type: ignore[dict-item]
    chance: dict[str, object] = {**CHANCE, "market": market}
    for key, value in changes.items():
        target = market if key in market or key in ("bid_min", "ask_min") else chance
        if key == "bid_min":
            market["bid"] = {"currency": "KRW", "min_total": value}
        elif key == "ask_min":
            market["ask"] = {"currency": "BTC", "min_total": value}
        elif value is None:
            del target[key]
        else:
            target[key] = value
    return chance


@pytest.mark.parametrize(
    "body",
    [
        [],
        _chance(market=None),
        _chance(id="KRW-ETH"),
        _chance(id=1),
        _chance(bid_fee=None),
        _chance(bid_fee=0.0005),
        _chance(ask_fee="1"),
        _chance(bid_fee="-0.0005"),
        _chance(bid_min="0"),
        _chance(ask_min=None),
        _chance(max_total="0"),
        _chance(state=None),
    ],
)
async def test_invalid_order_chance_rejected(body: object) -> None:
    with pytest.raises(UpbitResponseError):
        await _call(Upbit(body=body), "get_order_chance", "KRW-BTC")


async def test_authenticated_post_is_not_supported_yet() -> None:
    upbit = Upbit(body={})
    async with UpbitClient(transport=httpx.MockTransport(upbit), credentials=CREDENTIALS) as client:
        # 주문(POST)은 다음 단계에서 만든다. 그 전에는 인증 POST를 보내지 않는다
        with pytest.raises(NotImplementedError):
            await client._request("POST", "/v1/orders", {"market": "KRW-BTC"}, auth=True)

    assert upbit.requests == []


async def test_query_hash_matches_sent_query_with_array_and_special_values() -> None:
    upbit = Upbit(body=[])
    params = {"market": "KRW-BTC", "states[]": ["wait", "watch"], "limit": 10, "note": "a b+c"}
    async with UpbitClient(transport=httpx.MockTransport(upbit), credentials=CREDENTIALS) as client:
        await client._request("GET", "/v1/orders/open", params, auth=True)

    # httpx가 쿼리 문자열을 다시 인코딩하면 토큰의 해시와 실제 요청이 어긋난다. 보낸 그대로 해시와 일치해야 한다
    sent = upbit.requests[0].url.query.decode()
    assert sent == "market=KRW-BTC&states%5B%5D=wait&states%5B%5D=watch&limit=10&note=a+b%2Bc"
    assert _payload(upbit.requests[0])["query_hash"] == hashlib.sha512(unquote(sent).encode()).hexdigest()


class CountingLimiter(RateLimiter):
    def __init__(self) -> None:
        super().__init__(1000)
        self.count = 0

    async def acquire(self) -> None:
        self.count += 1
        await super().acquire()


async def test_account_queries_use_default_group_limiter() -> None:
    limiters = {name: CountingLimiter() for name in ("candle", "default", "order", "order_test")}
    handler = Upbit(body=ACCOUNTS)
    client = UpbitClient(
        transport=httpx.MockTransport(
            lambda request: handler(request) if request.url.path == "/v1/accounts" else httpx.Response(200, json=CHANCE)
        ),
        credentials=CREDENTIALS,
        candle_limiter=limiters["candle"],
        default_limiter=limiters["default"],
        order_limiter=limiters["order"],
        order_test_limiter=limiters["order_test"],
    )
    async with client:
        await client.get_accounts()
        await client.get_order_chance("KRW-BTC")

    # 업비트는 그룹마다 따로 센다. 계좌·주문 가능 정보는 default 그룹이다
    assert {name: limiter.count for name, limiter in limiters.items()} == {
        "candle": 0,
        "default": 2,
        "order": 0,
        "order_test": 0,
    }


def test_group_limits_stay_below_upbit_limits() -> None:
    # 업비트 문서 "요청 수 제한": default 초당 30회, order 초당 12회, order-test 초당 8회 (포켓 단위)
    assert DEFAULT_GROUP_REQUESTS_PER_SECOND < 30
    assert ORDER_GROUP_REQUESTS_PER_SECOND < 12
    assert ORDER_TEST_GROUP_REQUESTS_PER_SECOND < 8


def test_each_group_has_its_own_limiter() -> None:
    client = UpbitClient(credentials=CREDENTIALS)

    limiters = [client._candle_limiter, client._default_limiter, client._order_limiter, client._order_test_limiter]
    assert len({id(limiter) for limiter in limiters}) == 4
