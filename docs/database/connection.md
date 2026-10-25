# 데이터베이스 연결 설계

upbit-bot이 PostgreSQL에 연결하고 쿼리를 실행하는 구조를 정리한 문서입니다.
테이블 구조는 [DB 스키마](schema.md)를 참고하세요.

---

## 목차

1. [요구사항](#1-요구사항)
2. [참고한 사례](#2-참고한-사례)
3. [드라이버 선택](#3-드라이버-선택)
4. [구성](#4-구성)
5. [Database 클래스](#5-database-클래스)
6. [설정](#6-설정)
7. [타입 변환](#7-타입-변환)
8. [Repository 규칙](#8-repository-규칙)
9. [트랜잭션 경계](#9-트랜잭션-경계)
10. [메모리 사본](#10-메모리-사본)
11. [마켓 잠금](#11-마켓-잠금)
12. [오류 처리](#12-오류-처리)
13. [생명주기](#13-생명주기)
14. [테스트](#14-테스트)
15. [구현 순서](#15-구현-순서)

---

## 1. 요구사항

| 요구사항 | 이유 |
|----------|------|
| 비동기 I/O | 웹소켓 이벤트로 동작하므로 웹소켓, 거래소 REST, DB를 asyncio 하나로 처리 |
| 커넥션 풀 | 마켓 단위 작업이 동시에 실행되고, 작업 1건이 커넥션 1개를 사용 |
| 여러 Repository가 하나의 트랜잭션 공유 | 체결 반영 시 `FILLS`, `ORDERS`, `POSITION`, `INDICATOR_INFO`를 한 번에 커밋 |
| SQL 직접 작성 | 스키마가 생성 컬럼, 트리거, 부분 유니크 인덱스, advisory lock, `ON CONFLICT` 등 PostgreSQL 기능에 의존 |
| 정확한 타입 | 금액은 `NUMERIC` ↔ `Decimal`, 시각은 `TIMESTAMPTZ` ↔ 시간대가 있는 `datetime` |
| 오류를 숨기지 않음 | 연결 실패나 쿼리 오류를 빈 값으로 돌려주지 않고 예외로 알림 |
| 작은 규모 | 마켓 수 개, 30분봉. 동시 작업은 마켓 수 + 실시간 처리 몇 건 수준 |

---

## 2. 참고한 사례

실시간 매매 오픈소스 프로젝트가 DB를 사용하는 방식입니다.

### 2.1 프로젝트별 방식

| 프로젝트 | DB 연결 | 매매 판단에 쓰는 상태 | 쓰기 방식 | 재시작 |
|----------|---------|----------------------|-----------|--------|
| freqtrade | 동기 SQLAlchemy. 엔진 1개 + `scoped_session` (API 요청마다, 그 외에는 스레드마다 세션) | DB (`Trade` 객체를 DB에 저장하고 전략도 DB에서 조회) | 직접 쓰고 바로 커밋 | DB에서 다시 읽음 |
| Hummingbot | 동기 SQLAlchemy. 기본 SQLite, 외부 DB 선택 가능 | 메모리 (거래소 커넥터) | `MarketsRecorder`가 주문 이벤트(생성·체결·취소·실패·완료)를 구독해, 메인 이벤트 루프에서 이벤트마다 짧은 트랜잭션으로 기록 | DB 기록 + 거래소 상태 |
| NautilusTrader | Redis 또는 PostgreSQL 백엔드 | 메모리 캐시 ("DB는 복구 수단") | 쓰기를 채널로 백그라운드 작업에 넘기고, `buffer_interval_ms`로 묶어서 쓸 수 있음. 종료 시 `dispose()`를 부르지 않으면 버퍼의 쓰기를 잃을 수 있음 | DB에서 복원 → 거래소와 대조 후 시작 |

### 2.2 공통 패턴

| 패턴 | 내용 |
|------|------|
| 빈도에 따라 상태 위치가 다름 | 저빈도(캔들 단위) 봇은 DB를 기준 상태로 쓰고, 고빈도 봇은 메모리를 기준으로 두고 DB를 기록·복구용으로 씀 |
| 이벤트 단위의 짧은 쓰기 | 주문·체결 이벤트마다 짧은 트랜잭션으로 기록. 긴 트랜잭션을 들고 있지 않음 |
| 재시작 시 거래소와 대조 | DB에서 상태를 복원한 뒤, 거래소의 실제 주문·잔고와 맞춰 보고 나서 매매 시작 |

### 2.3 이 프로젝트의 판단

upbit-bot은 30분봉, 마켓 수 개 규모의 저빈도 봇이므로 freqtrade에 가까운 방식을 따릅니다.

| 항목 | 채택 | 이유 |
|------|------|------|
| 기준 상태 | DB | 주문·체결·포지션을 DB 기준으로 판단해야 재시작해도 상태가 정확함 |
| 쓰기 방식 | 이벤트마다 바로 커밋하는 짧은 트랜잭션 ([9](#9-트랜잭션-경계)) | Hummingbot과 같은 방식 |
| 쓰기를 모았다가 나중에 저장 | 채택하지 않음 | 서버가 죽으면 주문 기록을 잃을 수 있음. 주문은 전송 전에 DB에 커밋해야 함 |
| 메모리 사용 | 열린 포지션의 읽기용 사본만 둠 ([10](#10-메모리-사본)) | 실시간 손절에서 체결 이벤트마다 DB를 읽지 않기 위해서. 기준은 여전히 DB |
| 재시작 | DB에서 읽은 뒤 미완료 주문 조회, 보유 수량 대사 | 세 프로젝트 공통 |

출처

- freqtrade: [persistence/models.py](https://raw.githubusercontent.com/freqtrade/freqtrade/develop/freqtrade/persistence/models.py), [Trade object 문서](https://codeclimate.com/github/freqtrade/freqtrade/docs/trade-object.md/source)
- Hummingbot: [markets_recorder.py](https://raw.githubusercontent.com/hummingbot/hummingbot/master/hummingbot/connector/markets_recorder.py), [외부 DB 연결](https://hummingbot.org/global-configs/external-db)
- NautilusTrader: [Cache 문서](https://raw.githubusercontent.com/nautechsystems/nautilus_trader/develop/docs/concepts/cache.md), [Redis cache 소스](https://nautilustrader.io/docs/core-latest/src/nautilus_infrastructure/redis/cache.rs.html)

---

## 3. 드라이버 선택

**asyncpg**를 사용합니다.

| 선택지 | 장점 | 단점 | 판단 |
|--------|------|------|------|
| **asyncpg** | asyncio 전용, 빠름, `NUMERIC`·`TIMESTAMPTZ` 변환이 기본 제공, 풀 내장. `strategy-trading-bot`에서 이미 사용 | 쿼리 파라미터가 `$1` 형식, JSONB는 변환 등록 필요 | **채택** |
| psycopg 3 (async) | 동기·비동기 모두 지원, 표준 DB-API에 가까움 | 풀이 별도 패키지(`psycopg_pool`) | 대안으로 충분하지만 기존 코드·경험 재사용을 위해 asyncpg 선택 |
| SQLAlchemy (async) | ORM, 쿼리 빌더 | 스키마가 SQL 기능 중심이라 ORM 이점이 적고 계층이 늘어남 | 사용 안 함 |
| 동기 드라이버 + 스레드 | 단순. freqtrade, Hummingbot이 사용 | 이 프로젝트는 웹소켓 이벤트 기반이라 asyncio와 맞지 않음 | 사용 안 함 |

---

## 4. 구성

```
src/
├── connections/
│   ├── __init__.py
│   └── database.py              # Database: 풀 생성·종료, acquire(), transaction()
└── repositories/
    ├── __init__.py
    ├── market_repository.py     # MARKET
    ├── strategy_config_repository.py  # STRATEGY_CONFIG
    ├── indicator_repository.py  # INDICATOR_INFO
    ├── position_repository.py   # POSITION
    ├── order_repository.py      # ORDERS
    └── fill_repository.py       # FILLS
```

| 계층 | 역할 | 하지 않는 것 |
|------|------|--------------|
| `Database` | 풀 관리, 커넥션 대여, 트랜잭션 시작 | 테이블별 쿼리 |
| Repository | 테이블별 SQL 실행, 결과를 모델로 변환 | 커넥션 생성, 커밋, 매매 판단 |
| 서비스 (매매 로직) | 트랜잭션 범위 결정, Repository 조합 | SQL 작성 |

`SIGNAL_HISTORY`는 트리거가 기록하므로 쓰기용 Repository는 두지 않습니다. 조회가 필요해지면 추가합니다.

---

## 5. Database 클래스

```python
class Database:
    def __init__(self, config: DatabaseConfig) -> None: ...

    async def connect(self) -> None:
        """풀을 만들고 연결을 확인한다 (SELECT 1). 실패하면 예외."""

    async def close(self) -> None:
        """풀의 모든 커넥션을 닫는다. 대여 중인 커넥션은 반납될 때까지 기다린다."""

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        """커넥션 1개를 빌린다. 블록이 끝나면 풀에 반납한다."""

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        """커넥션을 빌려 트랜잭션을 연다. 정상 종료면 커밋, 예외면 롤백 후 다시 던진다."""
```

### 5.1 동작 규칙

| 상황 | 동작 |
|------|------|
| `connect()` 전에 `acquire()`, `transaction()` 호출 | `RuntimeError` |
| 풀에 남은 커넥션이 없음 | `acquire_timeout`까지 기다린 뒤 `asyncio.TimeoutError` |
| 쿼리 실행 시간 초과 | `command_timeout`이 지나면 `asyncio.TimeoutError` |
| 트랜잭션 블록 안에서 예외 | 롤백 후 예외를 다시 던짐 |

### 5.2 커넥션 초기화

풀이 새 커넥션을 만들 때 아래를 설정합니다.

| 설정 | 방법 | 내용 |
|------|------|------|
| JSONB 변환 | `init` 콜백에서 `set_type_codec` | `jsonb` ↔ `dict` / `list` 변환 등록. 직렬화는 `orjson.dumps(value, default=str)` (아래 참고) |
| `application_name` | `create_pool(server_settings=…)` | `upbit-bot` (DB에서 어떤 프로세스의 커넥션인지 구분) |

`orjson`은 `datetime`, `UUID`는 직렬화하지만 `Decimal`은 직렬화하지 못하고 `TypeError`를 냅니다. `signal_snapshot`에는 평단가·순수익률 같은 `Decimal` 값이 들어가므로 `default=str`로 문자열로 저장합니다. `float`로 바꾸지 않으므로 정밀도가 유지됩니다. (예: `Decimal('0.00010391915')` → `"0.00010391915"`)

### 5.3 `strategy-trading-bot`의 `PostgresPool`과 다른 점

| 항목 | `PostgresPool` | upbit-bot `Database` |
|------|----------------|----------------------|
| 연결 안 됐을 때 `fetch_all`, `fetch_one`, `execute` | `[]`, `None` 반환 | 해당 메서드를 두지 않음. `acquire()` / `transaction()`이 예외 |
| 풀에서 바로 쿼리 실행 | `fetch_all` 등 제공 (호출마다 다른 커넥션) | 제공하지 않음. 항상 커넥션을 받아 Repository에 전달 |
| JSONB | 변환 없음 | `init`에서 변환 등록 |
| 기본 풀 크기 | 최소 10, 최대 20 | 최소 2, 최대 10 (설정으로 변경) |
| 설정 | dict | `DatabaseConfig` (타입이 있는 dataclass) |

---

## 6. 설정

### 6.1 환경 변수

접속 정보는 `.env`에 둡니다. `.env.example`에는 값 없이 키만 둡니다.

| 환경 변수 | 예시 | 설명 |
|-----------|------|------|
| `DB_HOST` | `localhost` | 호스트 |
| `DB_PORT` | `5432` | 포트 |
| `DB_NAME` | `upbit_bot` | 데이터베이스 이름 |
| `DB_USER` | `upbit_bot` | 사용자 |
| `DB_PASSWORD` | | 비밀번호 |

### 6.2 풀 설정

| 항목 | 기본값 | 설명 |
|------|--------|------|
| `pool_min_size` | 2 | 항상 유지하는 커넥션 수 |
| `pool_max_size` | 10 | 최대 커넥션 수 |
| `acquire_timeout` | 10초 | 커넥션을 빌릴 때 최대 대기 시간 |
| `command_timeout` | 30초 | 쿼리 1건의 최대 실행 시간 |
| `max_inactive_connection_lifetime` | 300초 | 쓰지 않는 커넥션을 닫기까지의 시간 |
| `max_concurrent_markets` | 5 | 동시에 실행하는 캔들 마감 처리 수 ([11.3](#113-동시-실행-제한)) |

- `pool_max_size` ≥ `max_concurrent_markets` + 여유분(체결 반영, 실시간 손절, 시작 작업용)이어야 합니다. 기본값은 5 + 5 = 10입니다.
- 마켓 작업 하나는 커넥션 1개만 씁니다. ([9.1](#91-마켓-작업-안의-트랜잭션))
- 비밀번호는 로그에 남기지 않습니다. 연결 로그에는 호스트, 포트, DB 이름만 남깁니다.

---

## 7. 타입 변환

| PostgreSQL | Python | 주의 |
|------------|--------|------|
| `NUMERIC` | `decimal.Decimal` | 넣을 때도 `Decimal`을 씁니다. `float`를 넘기면 부동소수점 오차가 그대로 저장됩니다. 거래소 응답의 문자열 숫자는 `Decimal(str)`로 바로 변환합니다 |
| `DOUBLE PRECISION` | `float` | 지표 값(`value`, `signal`)에만 사용 |
| `TIMESTAMPTZ` | `datetime` (시간대 있음) | 시간대가 없는 `datetime`은 넣지 않습니다. 캔들 시각(epoch ms)은 UTC `datetime`으로 바꿔서 넣습니다 |
| `INTEGER`, `BIGINT` | `int` | |
| `BOOLEAN` | `bool` | |
| `UUID` | `uuid.UUID` | `client_order_id` |
| `JSONB` | `dict` / `list` | [5.2](#52-커넥션-초기화)에서 변환 등록. 안에 넣은 `Decimal`은 문자열로 저장되므로, 읽을 때 모델에서 `Decimal(str)`로 되돌림 |
| `VARCHAR`, `TEXT` | `str` | |

---

## 8. Repository 규칙

```python
class OrderRepository:
    async def insert_pending(self, conn: asyncpg.Connection, order: NewOrder) -> Order:
        row = await conn.fetchrow(
            """
            INSERT INTO ORDERS (POSITION_ID, SIDE, ORD_TYPE, REASON, REQUESTED_AMOUNT, SIGNAL_SNAPSHOT)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING *
            """,
            order.position_id, order.side, order.ord_type, order.reason,
            order.requested_amount, order.signal_snapshot,
        )
        return Order.from_record(row)
```

| 규칙 | 내용 |
|------|------|
| 커넥션은 인자로 받음 | 모든 메서드의 첫 번째 인자는 `conn`. Repository가 직접 커넥션을 빌리지 않음 |
| 커밋하지 않음 | 트랜잭션 시작·커밋은 호출하는 쪽(서비스)이 결정 |
| 파라미터 바인딩 | 값은 항상 `$1`, `$2`로 전달. 문자열 포맷으로 SQL을 만들지 않음 |
| 모델로 반환 | `asyncpg.Record`를 그대로 내보내지 않고 모델(dataclass)로 바꿔서 반환 |
| 오류를 삼키지 않음 | `try/except`로 오류를 숨기지 않음. 제약 위반(중복 등)을 정상 흐름으로 다뤄야 할 때만 특정 예외를 잡거나 `ON CONFLICT`를 사용 |
| 상태 없음 | Repository는 상태를 갖지 않아 하나의 인스턴스를 공유 |

---

## 9. 트랜잭션 경계

트랜잭션 하나로 묶어야 하는 작업입니다. 나머지 단일 쿼리는 `acquire()`로 빌린 커넥션에서 자동 커밋으로 실행합니다.

| 작업 | 트랜잭션 안에서 하는 일 |
|------|-------------------------|
| 마켓 등록 | `MARKET` INSERT + `INDICATOR_INFO` 5행 INSERT |
| 캔들 재생 | 처리할 마감 캔들 전체의 `INDICATOR_INFO` 갱신, 상태 초기화 |
| 주문 기록 | (신규 매수면) `POSITION` INSERT + `ORDERS` INSERT (`pending`) |
| 체결 반영 | 주문 행 잠금(`SELECT … FOR UPDATE`) → `FILLS` INSERT → `ORDERS` 갱신 → `POSITION` 갱신 → `INDICATOR_INFO` 상태 초기화 |

```python
async with db.transaction() as conn:
    order = await order_repo.lock(conn, order_id)        # SELECT … FOR UPDATE
    if order.is_applied:
        return
    await fill_repo.insert_many(conn, fills)              # ON CONFLICT (TRADE_UUID) DO NOTHING
    await order_repo.mark_finished(conn, order_id, state, paid_fee)
    await position_repo.apply_fills(conn, order.position_id, ...)
    await indicator_repo.refresh(conn, market_id, types=ALL)
```

| 규칙 | 내용 |
|------|------|
| 트랜잭션 안에서 외부 API 호출 금지 | 거래소 주문 전송, 시세 조회는 트랜잭션 밖에서 함. 네트워크 대기 동안 행 잠금을 쥐고 있지 않기 위해서 |
| 주문 기록은 전송 전에 커밋 | 주문 기록 트랜잭션을 커밋한 뒤 주문을 전송. 전송 직후 서버가 죽어도 기록이 남음 |
| 짧게 유지 | 트랜잭션 안에서는 DB 작업만 함 |

### 9.1 마켓 작업 안의 트랜잭션

[마켓 잠금](#11-마켓-잠금)을 잡은 작업은 **잠금을 잡은 커넥션에서** 트랜잭션을 엽니다. 작업 안에서 `db.transaction()`을 부르면 풀에서 커넥션을 하나 더 빌리게 되어, 마켓 작업 하나가 커넥션 2개를 쓰게 됩니다. 30분봉은 모든 마켓이 같은 시각에 마감되므로 이 경우 풀이 쉽게 바닥납니다.

```python
async with market_lock(market_id) as conn:
    if conn is None:
        return
    async with conn.transaction():                     # 잠금 커넥션에서 트랜잭션
        await indicator_repo.replay(conn, market_id, candles)
    # 판단 → 주문 기록도 같은 conn 으로
```

| 사용처 | 트랜잭션을 여는 방법 |
|--------|----------------------|
| 마켓 잠금 안 (캔들 마감 처리, 실시간 손절) | `conn.transaction()` (잠금 커넥션) |
| 마켓 잠금 밖 (체결 반영, 서버 시작 작업) | `db.transaction()` |

---

## 10. 메모리 사본

실시간 손절은 체결 이벤트가 들어올 때마다 열린 포지션의 평단가와 보유 수량이 필요합니다. 이벤트마다 DB를 읽지 않도록 **읽기용 사본**을 메모리에 둡니다. 기준은 항상 DB입니다.

### 10.1 사본에 두는 값

| 키 | 값 |
|----|-----|
| `market_id` | 열린 포지션의 `position_id`, `avg_buy_price`, `holding_volume`, `buy_count` |
| `market_id` | 미완료 주문 여부 |
| `market_id` | 매매 중단 여부 (보유 수량 대사 불일치) |

### 10.2 규칙

| 규칙 | 내용 |
|------|------|
| DB가 기준 | 사본은 판단을 빠르게 거르는 용도. 주문을 내기 전에는 DB에서 다시 확인 |
| 커밋 후 갱신 | 사본은 DB 트랜잭션이 **커밋된 뒤에만** 갱신. 롤백된 내용이 사본에 남지 않게 함 |
| 갱신 시점 | 서버 시작 시 DB에서 전체 로드, 주문 기록·체결 반영 커밋 후 해당 마켓 갱신, 포지션이 닫히면 제거 |
| 주문 전 재확인 | 사본으로 손절 조건이 충족되면, [마켓 잠금](#11-마켓-잠금)을 얻은 뒤 DB에서 포지션과 미완료 주문을 다시 읽어 확인하고 주문 |
| 쓰기는 하지 않음 | 사본을 먼저 바꾸고 나중에 DB에 쓰는 방식은 쓰지 않음. DB 쓰기 → 커밋 → 사본 갱신 순서만 허용 |

### 10.3 사본이 어긋날 수 있는 경우

| 경우 | 대응 |
|------|------|
| 다른 프로세스가 같은 마켓의 체결을 반영 (blue/green 전환 중) | 주문 전 재확인에서 DB 값을 쓰므로 잘못 주문하지 않음. 재확인 후 사본도 DB 값으로 갱신 |
| 커밋 직후 사본 갱신 전에 오류 | 다음 캔들 마감 처리에서 DB를 읽을 때 사본을 다시 맞춤 |

---

## 11. 마켓 잠금

같은 마켓을 두 작업이 동시에 처리하지 않도록 두 가지 잠금을 함께 씁니다.

| 잠금 | 범위 | 막는 상황 |
|------|------|-----------|
| 마켓별 `asyncio.Lock` | 프로세스 내부 | 같은 프로세스 안에서 캔들 마감 처리와 실시간 손절이 겹침 |
| `pg_try_advisory_lock(1, market_id)` | 프로세스 간 | blue/green 배포 전환 중 서버 두 개가 같은 마켓을 처리 |

```python
@asynccontextmanager
async def market_lock(market_id: int) -> AsyncIterator[asyncpg.Connection | None]:
    async with local_locks[market_id]:
        async with db.acquire() as conn:
            locked = await conn.fetchval("SELECT pg_try_advisory_lock(1, $1)", market_id)
            if not locked:
                yield None              # 다른 프로세스가 처리 중 → 호출한 쪽이 건너뜀
                return
            try:
                yield conn              # 마켓 작업은 이 커넥션으로 진행
            finally:
                await conn.execute("SELECT pg_advisory_unlock(1, $1)", market_id)
```

| 규칙 | 내용 |
|------|------|
| 같은 커넥션 사용 | advisory lock은 잡은 커넥션에서만 풀 수 있어서, 잠금을 잡은 커넥션으로 마켓 작업을 계속 진행 |
| 키 | `(1, market_id)` 두 정수 형식. 첫 번째 값은 잠금 종류(1 = 마켓 작업). 나중에 다른 종류의 잠금을 추가해도 겹치지 않음 |
| 기다리지 않음 | `pg_try_advisory_lock`으로 바로 실패하고 건너뜀. 다음 이벤트에서 다시 시도 |
| 해제 보장 | `finally`에서 해제. 해제를 놓쳐도 asyncpg가 커넥션을 풀에 반납할 때 남은 advisory lock을 모두 풀어 줌 |

### 11.1 작업별 잠금 사용

잠금을 못 잡았을 때 건너뛰어도 되는 작업과 건너뛰면 안 되는 작업을 구분합니다.

| 작업 | 이벤트가 다시 오는가 | 잠금 |
|------|----------------------|------|
| 캔들 마감 처리 | 예 (다음 캔들 마감, 놓친 캔들은 재생) | `market_lock()`. 못 잡으면 건너뜀 |
| 실시간 손절 | 예 (다음 체결 이벤트) | `market_lock()`. 못 잡으면 건너뜀 |
| 체결 반영 | **아니오** (`myOrder` 이벤트는 한 번만 옴) | `market_lock()`을 쓰지 않음. 아래 방식으로 처리 |

체결 반영은 건너뛰지 않고 기다려서 처리합니다.

| 범위 | 방법 |
|------|------|
| 프로세스 내부 | 마켓별 `asyncio.Lock`을 **기다려서** 획득. 캔들 마감 처리가 끝나면 이어서 반영 |
| 프로세스 간 | advisory lock을 쓰지 않음. 주문 행 잠금(`SELECT … FOR UPDATE`) + 이미 반영된 주문 건너뛰기 + `FILLS` 중복 무시(`ON CONFLICT`)로, 두 프로세스가 같은 체결을 받아도 한 번만 반영 |
| 트랜잭션 | 잠금 밖이므로 `db.transaction()` 사용 ([9.1](#91-마켓-작업-안의-트랜잭션)) |

### 11.2 잠금이 풀려도 막는 DB 제약

advisory lock은 커넥션 단위라서, 마켓 작업 도중 DB 연결이 끊기면 잠금도 함께 풀립니다. 그 사이 다른 프로세스가 같은 마켓을 처리하면 두 프로세스가 동시에 "미완료 주문 없음"을 확인하고 주문할 수 있습니다. 그래서 중복 주문은 잠금이 아니라 **DB 제약**으로 최종적으로 막습니다.

| 제약 | 막는 상황 |
|------|-----------|
| `POSITION (market_id) WHERE status = 'OPEN'` 부분 유니크 인덱스 | 같은 마켓의 신규 매수 중복 |
| `ORDERS (position_id) WHERE state IN ('pending', 'wait', 'watch')` 부분 유니크 인덱스 | 같은 포지션의 추가 매수·매도 중복 (포지션당 미완료 주문 1개) |

- 주문 기록은 전송 전에 커밋하므로, 두 번째 작업은 기록 단계에서 유니크 제약 위반(`UniqueViolationError`)으로 실패하고 주문을 보내지 않습니다.
- 이 위반은 "다른 작업이 이미 주문 중"이라는 뜻이므로 오류가 아니라 건너뛰기로 처리합니다. 이 예외만 골라서 잡습니다.

### 11.3 동시 실행 제한

모든 마켓이 같은 시각에 마감되면 마켓 수만큼 작업이 동시에 시작되어 커넥션과 업비트 REST 요청이 한꺼번에 몰립니다. 캔들 마감 처리는 `asyncio.Semaphore(max_concurrent_markets)`로 동시 실행 수를 제한합니다.

```python
async with market_semaphore:                 # 1. 동시 실행 수 제한 (커넥션을 빌리기 전에)
    async with market_lock(market_id) as conn:  # 2. 마켓 잠금 + 커넥션 1개
        ...
```

| 규칙 | 내용 |
|------|------|
| 순서 | 세마포어를 먼저 얻고 커넥션을 빌림. 커넥션을 쥔 채로 세마포어를 기다리지 않음 |
| 대상 | 캔들 마감 처리만 제한. 실시간 손절과 체결 반영은 세마포어 없이 처리 (풀 여유분 사용) |

---

## 12. 오류 처리

| 오류 | 처리 |
|------|------|
| 시작 시 연결 실패 | 예외를 던지고 서버 시작을 중단 |
| 실행 중 연결 끊김 | asyncpg 풀이 끊긴 커넥션을 버리고 새로 만듦. 실패한 작업은 예외로 끝나고, 다음 이벤트에서 다시 처리 |
| 트랜잭션 중 오류 | 롤백 후 예외를 다시 던짐. 작업 단위(마켓)에서 로그를 남기고 종료 |
| 제약 위반 | 대부분 버그이므로 그대로 예외. 의도된 중복(같은 체결 재수신 등)은 `ON CONFLICT DO NOTHING`으로, 미완료 주문 중복은 건너뛰기로 처리 ([11.2](#112-잠금이-풀려도-막는-db-제약)) |
| 풀 고갈 | `acquire_timeout` 후 예외. 경고 로그 (풀 크기 또는 잠금 보유 시간 점검 대상) |

- 쿼리 자동 재시도는 하지 않습니다. 캔들 마감 처리와 손절은 다음 이벤트에서 처음부터 다시 처리됩니다.
- 체결 반영이 실패하면 주문이 미완료 상태로 남고, 미완료 주문 확인(REST 조회)에서 다시 반영됩니다.

---

## 13. 생명주기

| 시점 | 동작 |
|------|------|
| 서버 시작 | `Database.connect()` → `SELECT 1`로 확인 → [메모리 사본](#10-메모리-사본) 로드. 이후 마켓 등록, 미완료 주문 복구 등 시작 작업 |
| 실행 중 | 작업마다 `acquire()` / `transaction()` / `market_lock()` 사용 |
| 종료 신호 (`SIGTERM`, `SIGINT`) | 새 작업 받지 않음 → 진행 중인 작업 종료 대기 → `Database.close()` |

---

## 14. 테스트

| 대상 | 방식 |
|------|------|
| `Database` | asyncpg를 mock으로 대체한 단위 테스트. 연결 전 호출 시 예외, 트랜잭션 커밋·롤백, 반납 확인 |
| Repository | **실제 PostgreSQL**에 대한 통합 테스트. 트리거, 생성 컬럼, 제약 조건을 함께 확인 |
| 마켓 잠금 | 실제 PostgreSQL에서 커넥션 2개로 동시 획득 시 하나만 성공하는지 확인 |
| 메모리 사본 | 커밋 후에만 갱신되는지, 롤백 시 사본이 바뀌지 않는지 확인 |
| 체결 반영 | 커넥션 2개로 같은 주문의 체결을 동시에 반영해도 한 번만 반영되는지 확인 |
| 중복 주문 제약 | 같은 포지션에 미완료 주문을 두 번 기록하면 두 번째가 거부되는지 확인 |
| JSONB 변환 | `Decimal`이 들어간 값을 저장하고 읽었을 때 값이 그대로인지 확인 |

통합 테스트 환경

- 로컬: `docker-compose`로 PostgreSQL 실행
- CI: GitHub Actions의 PostgreSQL 서비스 컨테이너
- 테스트마다 트랜잭션을 열고 끝나면 롤백해 데이터를 남기지 않음
- `pytest.ini`의 `integration` 마커로 구분해, DB가 없는 환경에서는 건너뛸 수 있게 함

---

## 15. 구현 순서

커밋 가이드에 맞춰 작은 단위로 나눕니다.

| 순서 | 작업 |
|------|------|
| 1 | `asyncpg`, `orjson` 의존성 추가 |
| 2 | `DatabaseConfig` (환경 변수 로드) + 테스트 |
| 3 | `Database` 연결·종료 + 테스트 |
| 4 | `acquire()`, `transaction()` + 테스트 |
| 5 | JSONB 변환 등록 + 테스트 |
| 6 | 로컬 PostgreSQL `docker-compose` 추가 |
| 7 | `db/schema.sql` 추가 (로컬 PostgreSQL 시작 시 스키마 생성) |
| 8 | `market_lock()`, 동시 실행 제한 + 통합 테스트 |
| 9 | Repository별 구현 + 통합 테스트 (테이블마다 커밋) |
| 10 | 열린 포지션 메모리 사본 + 테스트 |
