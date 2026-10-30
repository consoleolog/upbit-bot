import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Self

import httpx

from src.app.config import Timeframe
from src.exchange.account import Account, OrderChance
from src.exchange.candle import Candle
from src.exchange.rate_limiter import RateLimiter
from src.exchange.upbit_auth import UpbitCredentials, authorization, encode_query

BASE_URL = "https://api.upbit.com"

# 요청 하나의 전체 시간 제한(초). 근거 있는 값이 아니며 운영하며 조정한다
DEFAULT_TIMEOUT = 5.0

Method = Literal["GET", "POST"]

# 업비트 candle_date_time_utc 형식. 시간대 표시가 없는 UTC 시각이다
CANDLE_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"
# 업비트가 캔들을 한 번에 주는 최대 개수
MAX_CANDLES = 200
# 캔들 조회의 초당 요청 수. 업비트 Remaining-Req 헤더 기준 한도는 초당 10회(candles 그룹, IP별)이고,
# 우리와 업비트가 1초를 재는 시점이 어긋날 수 있어 여유를 둔다. 여유분은 근거 있는 값이 아니다
CANDLE_REQUESTS_PER_SECOND = 8
# 거래·자산(exchange) API의 초당 요청 수. 업비트 한도는 포켓 단위로 그룹마다 따로 센다 (업비트 문서 "요청 수 제한").
# 우리와 업비트가 1초를 재는 시점이 어긋날 수 있어 여유를 둔다. 여유분은 근거 있는 값이 아니다
# default 그룹(계좌·주문 가능 정보·주문 조회): 초당 30회
DEFAULT_GROUP_REQUESTS_PER_SECOND = 25
# order 그룹(주문 생성): 초당 12회
ORDER_GROUP_REQUESTS_PER_SECOND = 10
# order-test 그룹(주문 생성 테스트): 초당 8회
ORDER_TEST_GROUP_REQUESTS_PER_SECOND = 6
# 업비트 캔들 경계는 UTC 기준으로 timeframe마다 나뉜다 (실제 응답으로 확인)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class UpbitError(Exception):
    """업비트 요청 실패. 상태 코드와 업비트 오류 이름·메시지를 담는다."""

    def __init__(self, message: str, *, status: int | None = None, name: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.name = name


class UpbitConnectionError(UpbitError):
    """거래소에 연결하지 못함. 요청은 거래소에 가지 않았다."""


class UpbitUnknownResultError(UpbitError):
    """요청을 보낸 뒤 응답을 받지 못함. 거래소가 요청을 처리했는지 알 수 없다.

    주문이면 다시 보내지 말고 identifier로 조회해 접수 여부를 확인해야 한다 (매매 로직 5.1).
    """


class UpbitTimeoutError(UpbitUnknownResultError):
    """시간 제한 안에 응답을 받지 못함."""


class UpbitResponseError(UpbitError):
    """성공 응답의 본문을 해석할 수 없음."""


class UpbitRequestError(UpbitError):
    """400: 요청·주문 오류 (잔고 부족, 최소 주문 금액 미달 등)."""


class UpbitAuthError(UpbitError):
    """401: 인증 오류."""


class UpbitForbiddenError(UpbitError):
    """403 또는 out_of_scope: API 키 권한이나 보안 설정 때문에 허용되지 않음."""


class UpbitNotFoundError(UpbitError):
    """404: 요청한 주문 등을 찾을 수 없음 (예: order_not_found)."""


class UpbitBlockedError(UpbitError):
    """418: 요청 수 제한을 계속 어겨 IP가 차단됨."""


class UpbitRateLimitError(UpbitError):
    """429: 요청 수 제한 초과."""


class UpbitServerError(UpbitError):
    """5xx: 거래소 장애."""


class UpbitServerResultUnknownError(UpbitServerError, UpbitUnknownResultError):
    """주문 등 상태를 바꾸는 요청에서 5xx를 받음. 거래소가 요청을 처리했는지 알 수 없다."""


class UpbitResponseResultUnknownError(UpbitResponseError, UpbitUnknownResultError):
    """주문 등 상태를 바꾸는 요청에 성공 응답을 받았지만 본문을 해석할 수 없음. 요청은 처리됐을 수 있다."""


# 연결 단계에서 실패해 요청이 거래소에 가지 않은 것이 확실한 오류.
# 여기에 없는 오류는 보낸 뒤 실패했을 수 있으므로 결과를 모르는 것으로 본다
_NOT_SENT_ERRORS: tuple[type[httpx.TransportError], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.ProxyError,
    httpx.UnsupportedProtocol,
)

_ERRORS_BY_STATUS: dict[int, type[UpbitError]] = {
    400: UpbitRequestError,
    401: UpbitAuthError,
    403: UpbitForbiddenError,
    404: UpbitNotFoundError,
    418: UpbitBlockedError,
    429: UpbitRateLimitError,
}


class UpbitClient:
    """업비트 REST API 클라이언트. 계좌 조회 등 인증이 필요한 요청은 credentials가 있어야 한다."""

    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        candle_limiter: RateLimiter | None = None,
        credentials: UpbitCredentials | None = None,
        default_limiter: RateLimiter | None = None,
        order_limiter: RateLimiter | None = None,
        order_test_limiter: RateLimiter | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._base_url = base_url
        self._timeout = timeout
        self._transport = transport
        self._clock = clock
        self._candle_limiter = candle_limiter or RateLimiter(CANDLE_REQUESTS_PER_SECOND)
        self._credentials = credentials
        self._default_limiter = default_limiter or RateLimiter(DEFAULT_GROUP_REQUESTS_PER_SECOND)
        self._order_limiter = order_limiter or RateLimiter(ORDER_GROUP_REQUESTS_PER_SECOND)
        self._order_test_limiter = order_test_limiter or RateLimiter(ORDER_TEST_GROUP_REQUESTS_PER_SECOND)
        self._http: httpx.AsyncClient | None = None

    async def open(self) -> None:
        if self._http is not None:
            raise RuntimeError("client is already open")
        self._http = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout, transport=self._transport)

    async def close(self) -> None:
        """연결을 닫는다. 여러 번 호출해도 된다."""
        if self._http is not None:
            http, self._http = self._http, None
            await http.aclose()

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def get_candles(
        self, market: str, timeframe: Timeframe, to: datetime, count: int = MAX_CANDLES
    ) -> list[Candle]:
        """to 이전에 시작한 캔들을 과거 → 최신 순으로 최대 count개 돌려준다.

        업비트는 진행 중인 캔들도 주므로, to에 마감 시각을 넘겨 마감된 캔들만 받는다 (to는 포함하지 않음).
        """
        if to.tzinfo is None:
            raise ValueError("to must be timezone-aware")
        # 경계가 아니거나 미래인 to를 넘기면 start < to인 진행 중인 캔들이 섞인다
        if (to - EPOCH) % timeframe.duration or to > self._clock():
            raise ValueError(f"to must be a past {timeframe} candle boundary: {to.isoformat()}")
        if not 1 <= count <= MAX_CANDLES:
            raise ValueError(f"count must be between 1 and {MAX_CANDLES}")
        minutes = int(timeframe.duration.total_seconds()) // 60
        params = {"market": market, "count": count, "to": to.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
        await self._candle_limiter.acquire()
        raw = await self._request("GET", f"/v1/candles/minutes/{minutes}", params)
        if not isinstance(raw, list) or len(raw) > count:
            raise UpbitResponseError(f"unexpected candle response for {market}: {type(raw).__name__}")
        candles = sorted((_parse_candle(item) for item in raw), key=lambda candle: candle.start)
        for previous, candle in zip([None, *candles], candles, strict=False):
            # 같은 시각의 캔들이 두 번 오면 지표 계산에 두 번 들어간다
            if candle.market != market or candle.start >= to or (previous and previous.start == candle.start):
                raise UpbitResponseError(f"candle does not match request ({market}, to={to.isoformat()}): {candle}")
        return candles

    async def get_accounts(self) -> list[Account]:
        """보유한 화폐별 잔고. 인증이 필요하다."""
        await self._default_limiter.acquire()
        raw = await self._request("GET", "/v1/accounts", auth=True)
        if not isinstance(raw, list):
            raise UpbitResponseError(f"unexpected accounts response: {type(raw).__name__}")
        accounts = [_parse_account(item) for item in raw]
        currencies = [account.currency for account in accounts]
        if len(set(currencies)) != len(currencies):
            raise UpbitResponseError(f"accounts response has duplicate currencies: {currencies}")
        return accounts

    async def get_order_chance(self, market: str) -> OrderChance:
        """마켓의 수수료와 최소·최대 주문 금액 (매매 로직 5.2, 6.1). 인증이 필요하다."""
        await self._default_limiter.acquire()
        raw = await self._request("GET", "/v1/orders/chance", {"market": market}, auth=True)
        chance = _parse_order_chance(raw)
        if chance.market != market:
            raise UpbitResponseError(f"order chance does not match request ({market}): {chance.market}")
        return chance

    async def _request(
        self, method: Method, path: str, params: dict[str, Any] | None = None, *, auth: bool = False
    ) -> Any:
        """요청을 보내고 JSON 응답을 돌려준다. 실패하면 UpbitError. 재시도하지 않는다.

        auth면 JWT 인증 헤더를 붙인다. 오류 메시지에는 쿼리 문자열과 헤더를 넣지 않는다.
        """
        if self._http is None:
            raise RuntimeError("client is not open")
        headers: dict[str, str] = {}
        url = path
        if auth:
            if self._credentials is None:
                raise RuntimeError("upbit credentials are required for this request")
            if method != "GET":
                raise NotImplementedError("authenticated POST is not supported yet")
            # query_hash는 실제로 보내는 쿼리 문자열로 만들어야 하므로, 직접 붙여서 보낸다
            query = encode_query(params) if params else ""
            url = f"{path}?{query}" if query else path
            headers["Authorization"] = authorization(self._credentials, query)
            params = None
        try:
            # httpx의 timeout은 연결·쓰기·읽기 단계마다 따로 걸리므로, 요청 전체에도 한 번 더 건다
            async with asyncio.timeout(self._timeout):
                if method == "GET":
                    response = await self._http.get(url, params=params, headers=headers)
                else:
                    response = await self._http.post(url, json=params, headers=headers)
        except _NOT_SENT_ERRORS as error:
            raise UpbitConnectionError(f"{method} {path} failed to connect: {type(error).__name__}") from error
        except (TimeoutError, httpx.TimeoutException) as error:
            raise UpbitTimeoutError(f"{method} {path} timed out after {self._timeout}s") from error
        except httpx.TransportError as error:
            raise UpbitUnknownResultError(
                f"{method} {path} failed after sending; result unknown: {type(error).__name__}"
            ) from error

        if not response.is_success:
            raise _to_error(method, path, response)
        try:
            return response.json()
        except ValueError as error:
            # JSON 형식 오류(JSONDecodeError)와 UTF-8이 아닌 본문(UnicodeDecodeError) 모두 ValueError다
            if method == "GET":
                raise UpbitResponseError(
                    f"{method} {path} returned invalid JSON", status=response.status_code
                ) from error
            # 주문 등은 성공 응답을 받았다면 이미 처리된 것이다. 못 읽었다고 실패로 보면 다시 보내 중복될 수 있다
            raise UpbitResponseResultUnknownError(
                f"{method} {path} returned invalid JSON; result unknown", status=response.status_code
            ) from error


def _to_error(method: str, path: str, response: httpx.Response) -> UpbitError:
    name, message = _parse_error_body(response)
    status = response.status_code
    if name == "out_of_scope":
        # 업비트 문서: 권한 오류는 API에 따라 401 또는 403으로 오므로 이름으로도 구분한다
        error_class: type[UpbitError] = UpbitForbiddenError
    elif status >= 500:
        # 조회는 다시 해도 되지만, 주문 등 상태를 바꾸는 요청은 5xx여도 처리됐을 수 있다
        error_class = UpbitServerError if method == "GET" else UpbitServerResultUnknownError
    else:
        error_class = _ERRORS_BY_STATUS.get(status, UpbitError)
    return error_class(f"{method} {path} failed ({status} {name}): {message}", status=status, name=name)


def _parse_error_body(response: httpx.Response) -> tuple[str | None, str]:
    """오류 응답에서 업비트 error.name, error.message를 꺼낸다. 형식이 다르면 본문 앞부분을 메시지로 쓴다."""
    try:
        body = response.json()
    except ValueError:
        # 429 등은 JSON이 아닌 본문이 올 수 있다. 깨진 본문이어도 상태 코드별 예외는 내야 한다
        return None, response.text[:200]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        name = error.get("name")
        return (str(name) if name is not None else None), str(error.get("message", ""))
    return None, response.text[:200]


def _parse_candle(raw: object) -> Candle:
    """업비트 캔들 응답 한 건을 Candle로 바꾼다. 형식이나 값이 잘못되면 UpbitResponseError."""
    try:
        if not isinstance(raw, dict) or not isinstance(raw["market"], str):
            raise TypeError("candle must be an object with a market code")
        start = datetime.strptime(raw["candle_date_time_utc"], CANDLE_TIME_FORMAT).replace(tzinfo=UTC)
        open_, high, low, close = (
            parse_price(raw[key]) for key in ("opening_price", "high_price", "low_price", "trade_price")
        )
        if not low <= min(open_, close) <= max(open_, close) <= high:
            raise ValueError("prices must satisfy low <= open, close <= high")
    except (KeyError, TypeError, ValueError) as error:
        raise UpbitResponseError(f"invalid upbit candle: {raw!r}") from error
    return Candle(market=raw["market"], start=start, open=open_, high=high, low=low, close=close)


def parse_price(value: object) -> Decimal:
    """업비트 응답의 가격을 Decimal로 바꾼다. 숫자가 아니거나 0 이하·무한대·NaN이면 예외."""
    # bool은 int의 하위 타입이라 따로 막는다. float는 문자열을 거쳐 표기된 값 그대로 바꾼다
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise TypeError(f"price must be a number: {value!r}")
    price = value if isinstance(value, Decimal) else Decimal(str(value))
    if not price.is_finite() or price <= 0:
        raise ValueError(f"price must be positive: {value!r}")
    return price


def _parse_account(raw: object) -> Account:
    """업비트 계좌 응답 한 건을 Account로 바꾼다. 형식이나 값이 잘못되면 UpbitResponseError."""
    try:
        if not isinstance(raw, dict) or not isinstance(raw["currency"], str) or not raw["currency"]:
            raise TypeError("account must be an object with a currency")
        if not isinstance(raw["unit_currency"], str):
            raise TypeError("unit_currency must be a string")
        return Account(
            currency=raw["currency"],
            balance=_amount(raw["balance"]),
            locked=_amount(raw["locked"]),
            avg_buy_price=_amount(raw["avg_buy_price"]),
            unit_currency=raw["unit_currency"],
        )
    except (KeyError, TypeError, ValueError, ArithmeticError) as error:
        raise UpbitResponseError(f"invalid upbit account: {raw!r}") from error


def _parse_order_chance(raw: object) -> OrderChance:
    """업비트 주문 가능 정보 응답을 OrderChance로 바꾼다. 형식이나 값이 잘못되면 UpbitResponseError."""
    try:
        if not isinstance(raw, dict) or not isinstance(raw["market"], dict):
            raise TypeError("order chance must be an object with a market")
        market = raw["market"]
        if not isinstance(market["id"], str) or not isinstance(market["state"], str):
            raise TypeError("market id and state must be strings")
        bid_fee, ask_fee = _amount(raw["bid_fee"]), _amount(raw["ask_fee"])
        if bid_fee >= 1 or ask_fee >= 1:
            raise ValueError("fee rate must be less than 1")
        chance = OrderChance(
            market=market["id"],
            bid_fee=bid_fee,
            ask_fee=ask_fee,
            bid_min_total=_amount(market["bid"]["min_total"]),
            ask_min_total=_amount(market["ask"]["min_total"]),
            max_total=_amount(market["max_total"]),
            state=market["state"],
        )
        if chance.bid_min_total <= 0 or chance.ask_min_total <= 0 or chance.max_total <= 0:
            raise ValueError("order totals must be positive")
        return chance
    except (KeyError, TypeError, ValueError, ArithmeticError) as error:
        raise UpbitResponseError(f"invalid upbit order chance: {raw!r}") from error


def _amount(value: object) -> Decimal:
    """업비트 거래·자산 응답의 숫자 문자열(금액·수량·비율)을 Decimal로 바꾼다. 음수·무한대·NaN이면 예외."""
    # 업비트는 이 값들을 정밀도를 지키려고 문자열로 준다 (예: "1000000.0"). 숫자가 오면 형식이 바뀐 것이다
    if not isinstance(value, str):
        raise TypeError(f"amount must be a numeric string: {value!r}")
    amount = Decimal(value)
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"amount must be a finite non-negative number: {value!r}")
    return amount
