"""업비트 인증 (JWT). 거래·자산 API 요청에 붙이는 Authorization 헤더를 만든다.

업비트 개발자 문서의 "인증" 기준: HS512로 서명하고, 파라미터가 있으면 URL 인코딩하지 않은 쿼리 문자열의 SHA512를
query_hash로 넣는다. 쿼리 문자열은 실제 요청과 같은 순서여야 한다.
"""

import hashlib
import os
import uuid
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Self
from urllib.parse import unquote, urlencode

import jwt

# PyJWT는 HS512 키가 64바이트보다 짧으면 InsecureKeyLengthWarning을 낸다. 업비트 비밀키 길이는 업비트가 정하므로
# 우리가 늘릴 수 없고, 요청 때마다 같은 경고가 로그에 남지 않도록 이 경고만 무시한다
warnings.filterwarnings("ignore", category=jwt.InsecureKeyLengthWarning)


@dataclass(frozen=True, kw_only=True)
class UpbitCredentials:
    """업비트 API 키. secret_key는 repr·로그·예외 메시지에 남기지 않는다."""

    access_key: str
    secret_key: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.access_key or not self.secret_key:
            raise ValueError("upbit access key and secret key must not be empty")

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Self:
        """UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY 환경 변수에서 읽는다."""
        keys = ("UPBIT_ACCESS_KEY", "UPBIT_SECRET_KEY")
        missing = [key for key in keys if not env.get(key)]
        if missing:
            raise ValueError(f"missing environment variables: {missing}")
        return cls(access_key=env["UPBIT_ACCESS_KEY"], secret_key=env["UPBIT_SECRET_KEY"])


def encode_query(params: Mapping[str, object]) -> str:
    """요청에 붙이는 쿼리 문자열 (URL 인코딩). 배열 값은 같은 키를 반복한다 (uuids[]=a&uuids[]=b)."""
    return urlencode(params, doseq=True)


def authorization(credentials: UpbitCredentials, encoded_query: str = "") -> str:
    """Authorization 헤더 값. encoded_query는 실제 요청에 붙인 쿼리 문자열이다 (없으면 빈 문자열).

    nonce가 매번 달라 같은 요청이라도 토큰은 매번 새로 만든다.
    """
    payload: dict[str, str] = {"access_key": credentials.access_key, "nonce": str(uuid.uuid4())}
    if encoded_query:
        # 해시는 URL 인코딩하지 않은 쿼리 문자열로 만든다
        payload["query_hash"] = hashlib.sha512(unquote(encoded_query).encode("utf-8")).hexdigest()
        payload["query_hash_alg"] = "SHA512"
    return f"Bearer {jwt.encode(payload, credentials.secret_key, algorithm='HS512')}"
