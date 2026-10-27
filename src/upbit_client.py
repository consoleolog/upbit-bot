import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

import httpx

from src.candle import Candle

BASE_URL = "https://api.upbit.com"

# 요청 하나의 전체 시간 제한(초). 근거 있는 값이 아니며 운영하며 조정한다
DEFAULT_TIMEOUT = 5.0

Method = Literal["GET", "POST"]

# 업비트 candle_date_time_utc 형식. 시간대 표시가 없는 UTC 시각이다
CANDLE_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


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


class UpbitBlockedError(UpbitError):
    """418: 요청 수 제한을 계속 어겨 IP가 차단됨."""


class UpbitRateLimitError(UpbitError):
    """429: 요청 수 제한 초과."""


class UpbitServerError(UpbitError):
    """5xx: 거래소 장애."""


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
    418: UpbitBlockedError,
    429: UpbitRateLimitError,
}


class UpbitClient:
    """업비트 REST API 클라이언트. 지금은 요청과 오류 처리만 있다."""

    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._base_url = base_url
        self._timeout = timeout
        self._transport = transport
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

    async def _request(self, method: Method, path: str, params: dict[str, Any] | None = None) -> Any:
        """요청을 보내고 JSON 응답을 돌려준다. 실패하면 UpbitError. 재시도하지 않는다."""
        if self._http is None:
            raise RuntimeError("client is not open")
        try:
            # httpx의 timeout은 연결·쓰기·읽기 단계마다 따로 걸리므로, 요청 전체에도 한 번 더 건다
            async with asyncio.timeout(self._timeout):
                if method == "GET":
                    response = await self._http.get(path, params=params)
                else:
                    response = await self._http.post(path, json=params)
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
            raise UpbitResponseError(f"{method} {path} returned invalid JSON", status=response.status_code) from error


def _to_error(method: str, path: str, response: httpx.Response) -> UpbitError:
    name, message = _parse_error_body(response)
    status = response.status_code
    if status >= 500:
        error_class: type[UpbitError] = UpbitServerError
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
            _price(raw[key]) for key in ("opening_price", "high_price", "low_price", "trade_price")
        )
        if not low <= min(open_, close) <= max(open_, close) <= high:
            raise ValueError("prices must satisfy low <= open, close <= high")
    except (KeyError, TypeError, ValueError) as error:
        raise UpbitResponseError(f"invalid upbit candle: {raw!r}") from error
    return Candle(market=raw["market"], start=start, open=open_, high=high, low=low, close=close)


def _price(value: object) -> Decimal:
    # bool은 int의 하위 타입이라 따로 막는다. float는 문자열을 거쳐 표기된 값 그대로 바꾼다
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"price must be a number: {value!r}")
    price = Decimal(str(value))
    if not price.is_finite() or price <= 0:
        raise ValueError(f"price must be positive: {value!r}")
    return price
