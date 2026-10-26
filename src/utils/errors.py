from collections.abc import Callable
from functools import wraps
from typing import Any

__all__ = [
    "BAD_REQUESTS",
    "TOO_MANY_REQ",
    "UNAUTHORIZED",
    "CreateAskError",
    "CreateBidError",
    "ExpiredAccessKeyError",
    "InValidAccessKeyError",
    "InsufficientFundsAskError",
    "InsufficientFundsBidError",
    "InvalidQueryPayloadError",
    "JwtVerificationError",
    "NoAutorizationIPError",
    "NonceUsedError",
    "OutOfScopeError",
    "RemainingReqParsingError",
    "TooManyRequestsError",
    "UnderMinTotalAskError",
    "UnderMinTotalBidError",
    "ValidationError",
    "WidthdrawAddressNotRegisterdError",
    "error_handler",
]


class UpbitBaseError(Exception):
    name: str
    code: int
    msg: str

    def __init__(self, **ctx: Any) -> None:
        self.__dict__ = ctx

    def __str__(self) -> str:
        return self.msg.format(**self.__dict__)


class UpbitError(UpbitBaseError):
    pass


class UpbitBadRequestError(UpbitBaseError):
    pass


class UpbitUnauthorizedError(UpbitBaseError):
    pass


class UpbitLimitError(UpbitBaseError):
    pass


class CreateAskError(UpbitBadRequestError):
    name = "create_ask_error"
    code = 400
    msg = "주문 요청 정보가 올바르지 않습니다."


class CreateBidError(UpbitBadRequestError):
    name = "create_bid_error"
    code = 400
    msg = "주문 요청 정보가 올바르지 않습니다."


class InsufficientFundsAskError(UpbitBadRequestError):
    name = "insufficient_funds_ask"
    code = 400
    msg = "매수/매도 가능 잔고가 부족합니다."


class InsufficientFundsBidError(UpbitBadRequestError):
    name = "insufficient_funds_bid"
    code = 400
    msg = "매수/매도 가능 잔고가 부족합니다."


class UnderMinTotalAskError(UpbitBadRequestError):
    name = "under_min_total_ask"
    code = 400
    msg = "주문 요청 금액이 최소 주문 금액 미만입니다."


class UnderMinTotalBidError(UpbitBadRequestError):
    name = "under_min_total_bid"
    code = 400
    msg = "주문 요청 금액이 최소 주문 금액 미만입니다."


class WidthdrawAddressNotRegisterdError(UpbitBadRequestError):
    name = "withdraw_address_not_registerd"
    code = 400
    msg = "허용되지 않은 출금 주소입니다."


class ValidationError(UpbitBadRequestError):
    name = "validation_error"
    code = 400
    msg = "잘못된 API 요청입니다."


class InvalidQueryPayloadError(UpbitUnauthorizedError):
    name = "invalid_query_payload"
    code = 401
    msg = "JWT 헤더의 페이로드가 올바르지 않습니다."


class JwtVerificationError(UpbitUnauthorizedError):
    name = "jwt_verification"
    code = 401
    msg = "JWT 토큰 검증에 실패했습니다."


class ExpiredAccessKeyError(UpbitUnauthorizedError):
    name = "expired_access_key"
    code = 401
    msg = "API 키가 만료되었습니다."


class NonceUsedError(UpbitUnauthorizedError):
    name = "nonce_used"
    code = 401
    msg = "이미 요청한 nonce값이 다시 사용되었습니다."


class NoAutorizationIPError(UpbitUnauthorizedError):
    name = "no_authorization_i_p"
    code = 401
    msg = "허용되지 않은 IP 주소입니다."


class OutOfScopeError(UpbitUnauthorizedError):
    name = "out_of_scope"
    code = 401
    msg = "허용되지 않은 기능입니다."


class TooManyRequestsError(UpbitLimitError):
    name = "Too many API requests."
    code = 429
    msg = "요청 수 제한을 초과했습니다."


class RemainingReqParsingError(UpbitLimitError):
    name = ""
    code = -1
    msg = "요청 수 제한 파싱에 실패했습니다."


class InValidAccessKeyError(UpbitUnauthorizedError):
    name = ""
    code = -1
    msg = "잘못된 엑세스 키입니다."


_ERROR_CLASSES = [
    CreateAskError,
    CreateBidError,
    InsufficientFundsAskError,
    InsufficientFundsBidError,
    UnderMinTotalAskError,
    UnderMinTotalBidError,
    WidthdrawAddressNotRegisterdError,
    ValidationError,
    InvalidQueryPayloadError,
    JwtVerificationError,
    ExpiredAccessKeyError,
    NonceUsedError,
    NoAutorizationIPError,
    OutOfScopeError,
    TooManyRequestsError,
    RemainingReqParsingError,
]

BAD_REQUESTS = [cls for cls in _ERROR_CLASSES if cls.code == 400]
UNAUTHORIZED = [cls for cls in _ERROR_CLASSES if cls.code == 401]
TOO_MANY_REQ = [cls for cls in _ERROR_CLASSES if cls.code == 429]


def error_handler(func: Callable) -> Callable:
    """
    업비트 API 응답의 에러를 처리하는 데코레이터.

    래핑된 함수가 반환한 aiohttp 응답 객체에서 JSON 을 파싱하고,
    에러가 포함되어 있으면 HTTP 상태 코드에 따라 적절한 예외를 발생시킨다.
    응답 처리 후 response.release() 를 호출하여 커넥션을 반환한다.

    Raises:
        UpbitBadRequestError: 400 에러 (주문 오류, 잔고 부족 등)
        UpbitUnauthorizedError: 401 에러 (인증 실패, 키 만료 등)
        TooManyRequestsError: 429 에러 (요청 수 제한 초과)
        UpbitError: 그 외 알 수 없는 에러
    """

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        response = await func(*args, **kwargs)
        try:
            data = await response.json()

            if isinstance(data, dict) and data.get("error"):
                error = data.get("error")

                name = error.get("name")
                message = error.get("message")

                code = response.status
                if code == 400:
                    for err in BAD_REQUESTS:
                        if err.name == name:
                            raise err()
                elif code == 401:
                    for err in UNAUTHORIZED:
                        if err.name == name:
                            raise err()
                elif code == 429:
                    for err in TOO_MANY_REQ:
                        text = await response.text()
                        if err.name == text:
                            raise TooManyRequestsError()
                else:
                    raise UpbitError(name=name, code=code, msg=message)

            return data
        finally:
            response.release()

    return wrapper
