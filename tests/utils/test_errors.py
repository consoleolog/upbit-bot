from unittest.mock import AsyncMock, MagicMock

import pytest

from src.utils.errors import (
    BAD_REQUESTS,
    TOO_MANY_REQ,
    UNAUTHORIZED,
    CreateAskError,
    CreateBidError,
    ExpiredAccessKeyError,
    InsufficientFundsAskError,
    InsufficientFundsBidError,
    InValidAccessKeyError,
    InvalidQueryPayloadError,
    JwtVerificationError,
    NoAutorizationIPError,
    NonceUsedError,
    OutOfScopeError,
    RemainingReqParsingError,
    TooManyRequestsError,
    UnderMinTotalAskError,
    UnderMinTotalBidError,
    UpbitBadRequestError,
    UpbitBaseError,
    UpbitError,
    UpbitLimitError,
    UpbitUnauthorizedError,
    ValidationError,
    WidthdrawAddressNotRegisterdError,
    error_handler,
)

# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _mock_response(status: int, data: object, text: str = "") -> MagicMock:
    """aiohttp ClientResponse 모의 객체를 생성한다."""
    response = MagicMock()
    response.status = status
    response.json = AsyncMock(return_value=data)
    response.text = AsyncMock(return_value=text)
    response.release = MagicMock()
    return response


def _make_handler(response: MagicMock):
    """주어진 response를 반환하는 error_handler 래핑 코루틴을 반환한다."""

    @error_handler
    async def _call():
        return response

    return _call


# ---------------------------------------------------------------------------
# UpbitBaseError — 기본 동작
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_upbit_base_error_is_exception():
    """UpbitBaseError는 Exception을 상속한다."""
    assert issubclass(UpbitBaseError, Exception)


@pytest.mark.unit
def test_upbit_base_error_ctx_stored_in_dict():
    """키워드 인자가 __dict__에 저장된다."""
    err = UpbitBaseError(msg="오류", code=999, name="test")
    assert err.msg == "오류"
    assert err.code == 999
    assert err.name == "test"


@pytest.mark.unit
def test_upbit_base_error_str_returns_msg():
    """__str__은 msg를 반환한다."""
    err = UpbitBaseError(msg="단순 메시지", code=0, name="x")
    assert str(err) == "단순 메시지"


@pytest.mark.unit
def test_upbit_base_error_str_formats_placeholders():
    """msg의 포맷 플레이스홀더를 ctx 값으로 치환한다."""
    err = UpbitBaseError(msg="{ticker} 처리 실패", code=0, name="x", ticker="KRW-BTC")
    assert str(err) == "KRW-BTC 처리 실패"


@pytest.mark.unit
def test_upbit_base_error_can_be_raised_and_caught():
    """UpbitBaseError를 raise하고 catch할 수 있다."""
    with pytest.raises(UpbitBaseError):
        raise UpbitBaseError(msg="오류", code=0, name="x")


# ---------------------------------------------------------------------------
# 상속 계층 검증
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "cls, parent",
    [
        (UpbitError, UpbitBaseError),
        (UpbitBadRequestError, UpbitBaseError),
        (UpbitUnauthorizedError, UpbitBaseError),
        (UpbitLimitError, UpbitBaseError),
        (CreateAskError, UpbitBadRequestError),
        (CreateBidError, UpbitBadRequestError),
        (InsufficientFundsAskError, UpbitBadRequestError),
        (InsufficientFundsBidError, UpbitBadRequestError),
        (UnderMinTotalAskError, UpbitBadRequestError),
        (UnderMinTotalBidError, UpbitBadRequestError),
        (WidthdrawAddressNotRegisterdError, UpbitBadRequestError),
        (ValidationError, UpbitBadRequestError),
        (InvalidQueryPayloadError, UpbitUnauthorizedError),
        (JwtVerificationError, UpbitUnauthorizedError),
        (ExpiredAccessKeyError, UpbitUnauthorizedError),
        (NonceUsedError, UpbitUnauthorizedError),
        (NoAutorizationIPError, UpbitUnauthorizedError),
        (OutOfScopeError, UpbitUnauthorizedError),
        (TooManyRequestsError, UpbitLimitError),
        (RemainingReqParsingError, UpbitLimitError),
        (InValidAccessKeyError, UpbitUnauthorizedError),
    ],
)
def test_inheritance(cls, parent):
    """각 에러 클래스가 올바른 부모를 상속한다."""
    assert issubclass(cls, parent)


# ---------------------------------------------------------------------------
# 개별 에러 — code 속성
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "cls, expected_code",
    [
        (CreateAskError, 400),
        (CreateBidError, 400),
        (InsufficientFundsAskError, 400),
        (InsufficientFundsBidError, 400),
        (UnderMinTotalAskError, 400),
        (UnderMinTotalBidError, 400),
        (WidthdrawAddressNotRegisterdError, 400),
        (ValidationError, 400),
        (InvalidQueryPayloadError, 401),
        (JwtVerificationError, 401),
        (ExpiredAccessKeyError, 401),
        (NonceUsedError, 401),
        (NoAutorizationIPError, 401),
        (OutOfScopeError, 401),
        (TooManyRequestsError, 429),
    ],
)
def test_error_code(cls, expected_code):
    """에러 클래스의 code 속성이 올바르다."""
    assert cls.code == expected_code


@pytest.mark.unit
def test_create_ask_error_attributes():
    assert CreateAskError.name == "create_ask_error"
    assert "올바르지 않습니다" in CreateAskError.msg


@pytest.mark.unit
def test_insufficient_funds_ask_error_attributes():
    assert InsufficientFundsAskError.name == "insufficient_funds_ask"
    assert "잔고" in InsufficientFundsAskError.msg


@pytest.mark.unit
def test_too_many_requests_error_attributes():
    assert TooManyRequestsError.code == 429
    assert "제한" in TooManyRequestsError.msg


# ---------------------------------------------------------------------------
# 에러 발생 및 타입별 catch
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_bad_request_error_can_be_caught_by_parent():
    with pytest.raises(UpbitBadRequestError):
        raise CreateAskError()


@pytest.mark.unit
def test_unauthorized_error_can_be_caught_by_parent():
    with pytest.raises(UpbitUnauthorizedError):
        raise JwtVerificationError()


@pytest.mark.unit
def test_limit_error_can_be_caught_by_parent():
    with pytest.raises(UpbitLimitError):
        raise TooManyRequestsError()


# ---------------------------------------------------------------------------
# BAD_REQUESTS / UNAUTHORIZED / TOO_MANY_REQ 리스트
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_bad_requests_all_have_code_400():
    assert all(cls.code == 400 for cls in BAD_REQUESTS)


@pytest.mark.unit
def test_unauthorized_all_have_code_401():
    assert all(cls.code == 401 for cls in UNAUTHORIZED)


@pytest.mark.unit
def test_too_many_req_all_have_code_429():
    assert all(cls.code == 429 for cls in TOO_MANY_REQ)


@pytest.mark.unit
def test_bad_requests_contains_expected_errors():
    expected = {
        CreateAskError,
        CreateBidError,
        InsufficientFundsAskError,
        InsufficientFundsBidError,
        UnderMinTotalAskError,
        UnderMinTotalBidError,
        ValidationError,
    }
    assert expected.issubset(set(BAD_REQUESTS))


@pytest.mark.unit
def test_unauthorized_contains_expected_errors():
    expected = {JwtVerificationError, ExpiredAccessKeyError, NonceUsedError, NoAutorizationIPError, OutOfScopeError}
    assert expected.issubset(set(UNAUTHORIZED))


@pytest.mark.unit
def test_too_many_req_contains_too_many_requests_error():
    assert TooManyRequestsError in TOO_MANY_REQ


@pytest.mark.unit
def test_invalid_access_key_error_not_in_grouped_lists():
    """InValidAccessKeyError(code=-1)는 그룹 리스트에 포함되지 않는다."""
    assert InValidAccessKeyError not in BAD_REQUESTS
    assert InValidAccessKeyError not in UNAUTHORIZED
    assert InValidAccessKeyError not in TOO_MANY_REQ


# ---------------------------------------------------------------------------
# error_handler — 정상 응답
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_error_handler_returns_data_when_no_error():
    """에러 없는 응답은 data를 그대로 반환한다."""
    data = {"market": "KRW-BTC", "price": 10000}
    response = _mock_response(200, data)

    result = await _make_handler(response)()

    assert result == data


@pytest.mark.unit
@pytest.mark.asyncio
async def test_error_handler_returns_list_data():
    """리스트 응답도 그대로 반환한다."""
    data = [{"id": 1}, {"id": 2}]
    response = _mock_response(200, data)

    result = await _make_handler(response)()

    assert result == data


@pytest.mark.unit
@pytest.mark.asyncio
async def test_error_handler_release_called_on_success():
    """정상 응답에서도 release()가 호출된다."""
    response = _mock_response(200, {"ok": True})

    await _make_handler(response)()

    response.release.assert_called_once()


# ---------------------------------------------------------------------------
# error_handler — 400 에러
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cls",
    [
        CreateAskError,
        CreateBidError,
        InsufficientFundsAskError,
        InsufficientFundsBidError,
        UnderMinTotalAskError,
        UnderMinTotalBidError,
        ValidationError,
    ],
)
async def test_error_handler_raises_bad_request_by_name(cls):
    """400 응답에서 name이 일치하면 해당 에러를 발생시킨다."""
    data = {"error": {"name": cls.name, "message": cls.msg}}
    response = _mock_response(400, data)

    with pytest.raises(cls):
        await _make_handler(response)()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_error_handler_release_called_on_400_error():
    """400 에러 발생 시에도 release()가 호출된다."""
    data = {"error": {"name": CreateAskError.name, "message": ""}}
    response = _mock_response(400, data)

    with pytest.raises(UpbitBadRequestError):
        await _make_handler(response)()

    response.release.assert_called_once()


# ---------------------------------------------------------------------------
# error_handler — 401 에러
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cls", [JwtVerificationError, ExpiredAccessKeyError, NonceUsedError, NoAutorizationIPError, OutOfScopeError]
)
async def test_error_handler_raises_unauthorized_by_name(cls):
    """401 응답에서 name이 일치하면 해당 에러를 발생시킨다."""
    data = {"error": {"name": cls.name, "message": cls.msg}}
    response = _mock_response(401, data)

    with pytest.raises(cls):
        await _make_handler(response)()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_error_handler_release_called_on_401_error():
    """401 에러 발생 시에도 release()가 호출된다."""
    data = {"error": {"name": JwtVerificationError.name, "message": ""}}
    response = _mock_response(401, data)

    with pytest.raises(UpbitUnauthorizedError):
        await _make_handler(response)()

    response.release.assert_called_once()


# ---------------------------------------------------------------------------
# error_handler — 429 에러
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_error_handler_raises_too_many_requests():
    """429 응답에서 text가 name과 일치하면 TooManyRequestsError를 발생시킨다."""
    data = {"error": {"name": TooManyRequestsError.name, "message": ""}}
    response = _mock_response(429, data, text=TooManyRequestsError.name)

    with pytest.raises(TooManyRequestsError):
        await _make_handler(response)()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_error_handler_release_called_on_429_error():
    """429 에러 발생 시에도 release()가 호출된다."""
    data = {"error": {"name": TooManyRequestsError.name, "message": ""}}
    response = _mock_response(429, data, text=TooManyRequestsError.name)

    with pytest.raises(TooManyRequestsError):
        await _make_handler(response)()

    response.release.assert_called_once()


# ---------------------------------------------------------------------------
# error_handler — 알 수 없는 에러 코드
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_error_handler_raises_upbit_error_for_unknown_code():
    """정의되지 않은 에러 코드는 UpbitError를 발생시킨다."""
    data = {"error": {"name": "unknown_error", "message": "알 수 없는 오류"}}
    response = _mock_response(500, data)

    with pytest.raises(UpbitError):
        await _make_handler(response)()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_error_handler_upbit_error_carries_context():
    """UpbitError에 name, code, msg가 담긴다."""
    data = {"error": {"name": "some_error", "message": "서버 오류"}}
    response = _mock_response(503, data)

    with pytest.raises(UpbitError) as exc_info:
        await _make_handler(response)()

    err = exc_info.value
    assert err.name == "some_error"
    assert err.code == 503
    assert err.msg == "서버 오류"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_error_handler_release_called_on_unknown_error():
    """알 수 없는 에러 발생 시에도 release()가 호출된다."""
    data = {"error": {"name": "x", "message": "오류"}}
    response = _mock_response(500, data)

    with pytest.raises(UpbitError):
        await _make_handler(response)()

    response.release.assert_called_once()
