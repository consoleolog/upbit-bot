import hashlib
import uuid

import jwt
import pytest

from src.exchange.upbit_auth import UpbitCredentials, authorization, encode_query

SECRET = "secret-key-1-" + "x" * 64  # HS512 권장 길이(64바이트) 이상
CREDENTIALS = UpbitCredentials(access_key="access-key-1", secret_key=SECRET)


def _decode(header: str, secret: str = SECRET) -> dict[str, str]:
    assert header.startswith("Bearer ")
    return jwt.decode(header.removeprefix("Bearer "), secret, algorithms=["HS512"])


def test_token_without_query() -> None:
    header = authorization(CREDENTIALS)

    payload = _decode(header)
    assert set(payload) == {"access_key", "nonce"}
    assert payload["access_key"] == "access-key-1"
    uuid.UUID(payload["nonce"])
    assert jwt.get_unverified_header(header.removeprefix("Bearer "))["alg"] == "HS512"


def test_token_with_query_hashes_unencoded_query_string() -> None:
    query = encode_query({"market": "KRW-BTC", "states[]": ["wait", "watch"], "limit": 10})

    payload = _decode(authorization(CREDENTIALS, query))

    # 요청에는 URL 인코딩된 문자열을 붙이고, 해시는 인코딩하지 않은 문자열로 만든다 (업비트 인증 문서)
    assert query == "market=KRW-BTC&states%5B%5D=wait&states%5B%5D=watch&limit=10"
    expected = hashlib.sha512(b"market=KRW-BTC&states[]=wait&states[]=watch&limit=10").hexdigest()
    assert payload["query_hash"] == expected
    assert payload["query_hash_alg"] == "SHA512"


def test_nonce_changes_every_time() -> None:
    nonces = {_decode(authorization(CREDENTIALS))["nonce"] for _ in range(5)}

    assert len(nonces) == 5


def test_token_is_signed_with_secret_key() -> None:
    with pytest.raises(jwt.InvalidSignatureError):
        _decode(authorization(CREDENTIALS), secret="other-secret-" + "y" * 64)


def test_secret_key_is_not_in_repr() -> None:
    assert SECRET not in repr(CREDENTIALS)


@pytest.mark.parametrize(("access_key", "secret_key"), [("", "secret-value-xyz"), ("access-key-1", "")])
def test_empty_credentials_rejected(access_key: str, secret_key: str) -> None:
    with pytest.raises(ValueError, match="must not be empty") as error:
        UpbitCredentials(access_key=access_key, secret_key=secret_key)

    assert "secret-value-xyz" not in str(error.value)


def test_credentials_from_env() -> None:
    credentials = UpbitCredentials.from_env({"UPBIT_ACCESS_KEY": "a", "UPBIT_SECRET_KEY": "s"})

    assert (credentials.access_key, credentials.secret_key) == ("a", "s")


@pytest.mark.parametrize("missing", ["UPBIT_ACCESS_KEY", "UPBIT_SECRET_KEY"])
def test_credentials_from_env_missing(missing: str) -> None:
    env = {"UPBIT_ACCESS_KEY": "a", "UPBIT_SECRET_KEY": "s"}
    del env[missing]

    with pytest.raises(ValueError, match=missing):
        UpbitCredentials.from_env(env)
