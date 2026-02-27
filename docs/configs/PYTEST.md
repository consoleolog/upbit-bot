# Pytest 설정 가이드

> **Pytest**: Python 표준 테스트 프레임워크. 단위·통합·E2E 테스트를 간결하게 작성하고 실행합니다.

- 공식 문서: https://docs.pytest.org/en/stable/reference/customize.html
- 설정 파일: `pytest.ini` (프로젝트 루트)

---

## 목차

1. [기본 설정](#1-기본-설정)
2. [테스트 파일 패턴](#2-테스트-파일-패턴)
3. [addopts - 실행 옵션](#3-addopts---실행-옵션)
4. [asyncio_mode - 비동기 테스트](#4-asyncio_mode---비동기-테스트)
5. [markers - 테스트 분류](#5-markers---테스트-분류)
6. [filterwarnings - 경고 필터](#6-filterwarnings---경고-필터)
7. [실행 방법](#7-실행-방법)
8. [pytest-mock - 목 객체](#8-pytest-mock---목-객체)
9. [pytest-xdist - 병렬 실행](#9-pytest-xdist---병렬-실행)
10. [faker - 가짜 데이터 생성](#10-faker---가짜-데이터-생성)
11. [freezegun - 날짜/시간 고정](#11-freezegun---날짜시간-고정)
12. [httpx - 비동기 HTTP 클라이언트](#12-httpx---비동기-http-클라이언트)

---

## 1. 기본 설정

```ini
testpaths = tests
pythonpath = src
```

| 옵션           | 값       | 설명                            |
|--------------|---------|-------------------------------|
| `testpaths`  | `tests` | 테스트 탐색 디렉터리                   |
| `pythonpath` | `src`   | `src.` 접두사 없이 내부 모듈 import 가능 |

### pythonpath 효과

```python
# pythonpath = src 설정 시
from mymodule import MyClass      # Good

# 설정 없을 시
from src.mymodule import MyClass  # src. 접두사 필요
```

---

## 2. 테스트 파일 패턴

```ini
python_files = test_*.py
python_classes = Test*
python_functions = test_*
```

| 옵션                 | 패턴          | 설명             |
|--------------------|-------------|----------------|
| `python_files`     | `test_*.py` | 탐색할 테스트 파일명 패턴 |
| `python_classes`   | `Test*`     | 테스트 클래스명 패턴    |
| `python_functions` | `test_*`    | 테스트 함수명 패턴     |

### 예제 코드

```python
# tests/test_user.py

def test_create_user():       # 인식 O (test_* 패턴)
    pass

def create_user_test():       # 인식 X (패턴 불일치)
    pass

class TestUserService:        # 인식 O (Test* 패턴)
    def test_login(self):     # 인식 O
        pass

class UserServiceTest:        # 인식 X (패턴 불일치)
    pass
```

---

## 3. addopts - 실행 옵션

```ini
addopts =
    -v
    --tb=short
    --strict-markers
    -x
    --durations=5
    --cov=src
    --cov-report=term-missing
    --cov-report=html:htmlcov
    --cov-fail-under=80
```

### 출력 옵션

| 옵션           | 설명             |
|--------------|----------------|
| `-v`         | 테스트 이름 상세 출력   |
| `--tb=short` | 실패 트레이스백 짧게 출력 |

**-v 적용 전/후 비교**

```
# -v 없음
. . F .   (pass/pass/fail/pass 점으로만 출력)

# -v 있음
tests/test_user.py::test_create_user PASSED
tests/test_user.py::test_login PASSED
tests/test_user.py::test_delete_user FAILED
tests/test_user.py::test_update_user PASSED
```

### 실행 제어 옵션

| 옵션                 | 설명                |
|--------------------|-------------------|
| `--strict-markers` | 미등록 마커 사용 시 에러 발생 |
| `-x`               | 첫 번째 실패 즉시 중단     |
| `--durations=5`    | 가장 느린 테스트 5개 출력   |

**--durations=5 출력 예시**

```
slowest 5 durations
1.23s call     tests/test_api.py::test_large_request
0.87s call     tests/test_db.py::test_bulk_insert
0.45s setup    tests/test_auth.py::test_login
...
```

### 커버리지 옵션 (pytest-cov)

| 옵션                          | 설명                            |
|-----------------------------|-------------------------------|
| `--cov=src`                 | `src` 디렉터리 커버리지 측정            |
| `--cov-report=term-missing` | 미커버 라인 번호 터미널 출력              |
| `--cov-report=html:htmlcov` | HTML 리포트 생성 (`htmlcov/` 디렉터리) |
| `--cov-fail-under=80`       | 커버리지 80% 미만 시 테스트 실패          |

**term-missing 출력 예시**

```
---------- coverage: platform win32, python 3.13 ----------
Name                  Stmts   Miss  Cover   Missing
-----------------------------------------------------
src/service/user.py      45      9    80%   23-25, 47, 61-65
src/repository/user.py   30      0   100%
-----------------------------------------------------
TOTAL                    75      9    88%
```

---

## 4. asyncio_mode - 비동기 테스트

```ini
asyncio_mode = auto
```

| 값        | 설명                                 |
|----------|------------------------------------|
| `auto`   | `async def` 테스트 함수를 자동으로 비동기 실행    |
| `strict` | `@pytest.mark.asyncio` 마커를 명시해야 실행 |

### 예제 코드

```python
# asyncio_mode = auto 설정 시 마커 없이도 동작
async def test_fetch_user():
    result = await user_service.fetch(user_id=1)
    assert result.id == 1

# asyncio_mode = strict 설정 시 마커 필요
import pytest

@pytest.mark.asyncio
async def test_fetch_user():
    result = await user_service.fetch(user_id=1)
    assert result.id == 1
```

---

## 5. markers - 테스트 분류

```ini
markers =
    unit: 단위 테스트
    integration: 통합 테스트
    e2e: 엔드-투-엔드 테스트
```

| 마커            | 설명                          |
|---------------|-----------------------------|
| `unit`        | 단일 함수/클래스 단위 테스트            |
| `integration` | 여러 모듈 또는 DB·외부 서비스 연동 테스트   |
| `e2e`         | 전체 흐름을 실제 환경과 유사하게 검증하는 테스트 |

### 예제 코드

```python
import pytest

@pytest.mark.unit
def test_calculate_discount():
    assert calculate_discount(100, 0.1) == 90

@pytest.mark.integration
def test_save_user_to_db(db_session):
    user = User(name="홍길동")
    db_session.add(user)
    db_session.commit()
    assert db_session.query(User).count() == 1

@pytest.mark.e2e
def test_user_signup_flow(client):
    response = client.post("/signup", json={"name": "홍길동"})
    assert response.status_code == 201
```

### 마커별 선택 실행

```bash
# 단위 테스트만 실행
uv run pytest -m unit

# 통합 테스트 제외하고 실행
uv run pytest -m "not integration"

# 단위 + 통합 테스트 실행
uv run pytest -m "unit or integration"
```

---

## 6. filterwarnings - 경고 필터

```ini
filterwarnings =
    ignore::DeprecationWarning
    ignore::PendingDeprecationWarning
```

| 경고                          | 설명                                   |
|-----------------------------|--------------------------------------|
| `DeprecationWarning`        | 더 이상 사용하지 않는 기능 경고. 외부 라이브러리에서 자주 발생 |
| `PendingDeprecationWarning` | 향후 deprecated 예정인 기능 경고              |

---

## 7. 실행 방법

```bash
# 전체 테스트 실행
uv run pytest

# 특정 파일만 실행
uv run pytest tests/test_user.py

# 특정 함수만 실행
uv run pytest tests/test_user.py::test_create_user

# 마커별 실행
uv run pytest -m unit

# 커버리지 제외하고 빠르게 실행
uv run pytest --no-cov

# 병렬 실행 (pytest-xdist)
uv run pytest -n auto
```

---

## 8. pytest-mock - 목 객체

> `unittest.mock`을 pytest 스타일로 쉽게 사용할 수 있는 `mocker` fixture를 제공합니다.

- 공식 문서: https://pytest-mock.readthedocs.io/

### mocker.patch - 함수/메서드 교체

```python
def test_send_email(mocker):
    mock_send = mocker.patch("app.service.email.send_email", return_value=True)

    result = notification_service.notify(user_id=1)

    assert result is True
    mock_send.assert_called_once_with(to="user@example.com", subject="알림")
```

### mocker.patch.object - 객체의 메서드 교체

```python
def test_user_repository(mocker, user_repo):
    mock_find = mocker.patch.object(user_repo, "find_by_id", return_value=User(id=1, name="홍길동"))

    user = user_repo.find_by_id(1)

    assert user.name == "홍길동"
    mock_find.assert_called_once_with(1)
```

### mocker.MagicMock - 가짜 객체 생성

```python
def test_order_service(mocker):
    mock_payment = mocker.MagicMock()
    mock_payment.charge.return_value = {"status": "success"}

    order_service = OrderService(payment=mock_payment)
    result = order_service.place_order(amount=10000)

    assert result["status"] == "success"
    mock_payment.charge.assert_called_once_with(10000)
```

### side_effect - 예외 발생 시뮬레이션

```python
def test_retry_on_failure(mocker):
    mock_api = mocker.patch(
        "app.client.external_api",
        side_effect=[ConnectionError, ConnectionError, {"data": "ok"}]
    )

    result = retry_service.call_with_retry()

    assert result == {"data": "ok"}
    assert mock_api.call_count == 3
```

### spy - 실제 실행 + 호출 검증

```python
# patch와 달리 실제 함수를 실행하면서 호출 여부도 검증
def test_calculate(mocker):
    spy = mocker.spy(calculator, "add")

    result = calculator.add(1, 2)

    assert result == 3                # 실제 실행됨
    spy.assert_called_once_with(1, 2)
```

---

## 9. pytest-xdist - 병렬 실행

> 여러 CPU 코어를 활용하여 테스트를 병렬로 실행합니다.

- 공식 문서: https://pytest-xdist.readthedocs.io/

### 실행 방법

```bash
# CPU 코어 수에 맞게 자동으로 병렬 실행
uv run pytest -n auto

# 워커 수 직접 지정
uv run pytest -n 4

# 단일 프로세스로 실행 (디버깅 시)
uv run pytest -n 0
```

### 주의사항

병렬 실행 시 테스트 간 **공유 상태(DB, 파일, 전역 변수)**가 있으면 충돌이 발생할 수 있어요.

```python
# Bad - 전역 상태 공유 시 병렬 실행에서 충돌 발생 가능
counter = 0

def test_increment():
    global counter
    counter += 1
    assert counter == 1

# Good - 각 테스트가 독립적인 상태를 가짐
def test_increment():
    counter = Counter()
    counter.increment()
    assert counter.value == 1
```

---

## 10. faker - 가짜 데이터 생성

> 이름, 이메일, 주소 등 현실적인 테스트용 더미 데이터를 생성합니다.

- 공식 문서: https://faker.readthedocs.io/

### 기본 사용법

```python
from faker import Faker

fake = Faker("ko_KR")  # 한국어 로케일

print(fake.name())           # 홍길동
print(fake.email())          # gildong@example.com
print(fake.phone_number())   # 010-1234-5678
print(fake.address())        # 서울특별시 강남구 ...
```

### fixture로 활용

```python
import pytest
from faker import Faker

@pytest.fixture
def fake():
    return Faker("ko_KR")

def test_create_user(fake):
    user = User(
        name=fake.name(),
        email=fake.email(),
        phone=fake.phone_number(),
    )
    assert user.name is not None
    assert "@" in user.email
```

### 자주 쓰는 메서드

| 메서드                    | 예시 출력                         | 설명     |
|------------------------|-------------------------------|--------|
| `fake.name()`          | `홍길동`                         | 이름     |
| `fake.email()`         | `user@example.com`            | 이메일    |
| `fake.phone_number()`  | `010-1234-5678`               | 전화번호   |
| `fake.address()`       | `서울특별시 강남구 ...`               | 주소     |
| `fake.date_of_birth()` | `1990-05-21`                  | 생년월일   |
| `fake.uuid4()`         | `550e8400-e29b-41d4-a716-...` | UUID   |
| `fake.text()`          | `Lorem ipsum ...`             | 임의 텍스트 |
| `fake.random_int()`    | `42`                          | 랜덤 정수  |
| `fake.url()`           | `https://example.com`         | URL    |
| `fake.password()`      | `xK3#mP9!`                    | 패스워드   |

### 재현 가능한 데이터 (seed)

```python
fake = Faker()
Faker.seed(1234)  # 동일한 seed → 항상 같은 데이터 생성

print(fake.name())  # 항상 동일한 이름 출력
```

---

## 11. freezegun - 날짜/시간 고정

> `datetime.now()`, `date.today()` 등을 특정 시각으로 고정하여 시간에 의존하는 코드를 테스트합니다.

- 공식 문서: https://github.com/spulec/freezegun

### 데코레이터 방식

```python
from freezegun import freeze_time
from datetime import datetime

@freeze_time("2024-01-15 09:00:00")
def test_morning_greeting():
    result = greeting_service.get_greeting()
    assert result == "좋은 아침입니다!"

@freeze_time("2024-01-15 20:00:00")
def test_evening_greeting():
    result = greeting_service.get_greeting()
    assert result == "좋은 저녁입니다!"
```

### 컨텍스트 매니저 방식

```python
from freezegun import freeze_time

def test_token_expiry():
    with freeze_time("2024-01-01 00:00:00"):
        token = auth_service.create_token(expires_in=3600)

    with freeze_time("2024-01-01 01:30:00"):  # 1시간 30분 후
        result = auth_service.is_token_valid(token)
        assert result is False  # 만료됨
```

### fixture + 시간 이동

```python
import pytest
from freezegun import freeze_time

@pytest.fixture
def frozen_time():
    with freeze_time("2024-06-01 12:00:00") as frozen:
        yield frozen

def test_subscription_active(frozen_time):
    subscription = Subscription(expires_at="2024-12-31")
    assert subscription.is_active() is True

def test_subscription_expired(frozen_time):
    frozen_time.move_to("2025-01-01")
    subscription = Subscription(expires_at="2024-12-31")
    assert subscription.is_active() is False
```

### async 함수에서 사용

```python
from freezegun import freeze_time

@freeze_time("2024-03-01")
async def test_async_scheduled_job():
    result = await scheduler.run_daily_job()
    assert result.executed_at.date() == date(2024, 3, 1)
```

---

## 12. httpx - 비동기 HTTP 클라이언트

> async/sync HTTP 요청을 지원하며, FastAPI 등의 API 통합 테스트에 활용합니다.

- 공식 문서: https://www.python-httpx.org/

### 동기 요청

```python
import httpx

def test_get_user():
    with httpx.Client() as client:
        response = client.get("https://api.example.com/users/1")

    assert response.status_code == 200
    assert response.json()["id"] == 1
```

### 비동기 요청

```python
import httpx

async def test_get_users():
    async with httpx.AsyncClient() as client:
        response = await client.get("https://api.example.com/users")

    assert response.status_code == 200
    assert len(response.json()) > 0
```

### FastAPI 통합 테스트

```python
import pytest
import httpx
from app.main import app

@pytest.fixture
async def async_client():
    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        yield client

async def test_create_user(async_client):
    response = await async_client.post("/users", json={"name": "홍길동", "email": "test@test.com"})

    assert response.status_code == 201
    assert response.json()["name"] == "홍길동"

async def test_protected_endpoint(async_client):
    headers = {"Authorization": "Bearer test-token"}
    response = await async_client.get("/me", headers=headers)

    assert response.status_code == 200
```

### mock transport - 실제 요청 없이 테스트

```python
import httpx

def test_external_api_mock():
    def handler(request):
        return httpx.Response(200, json={"status": "ok"})

    transport = httpx.MockTransport(handler)

    with httpx.Client(transport=transport) as client:
        response = client.get("https://external-api.com/status")

    assert response.json() == {"status": "ok"}
```