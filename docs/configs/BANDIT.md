# Bandit 설정 가이드

> **Bandit**: Python 코드의 보안 취약점을 정적 분석하는 도구입니다.

- 공식 문서: https://bandit.readthedocs.io/en/latest/
- 설정 파일: `.bandit.yaml` (프로젝트 루트)

---

## 목차

1. [설정 파일 구조](#1-설정-파일-구조)
2. [심각도 & 신뢰도 레벨](#2-심각도--신뢰도-레벨)
3. [주요 규칙 및 예제 코드](#3-주요-규칙-및-예제-코드)
4. [비활성화 규칙](#4-비활성화-규칙)
5. [실행 방법](#5-실행-방법)

---

## 1. 설정 파일 구조

```yaml
# 검사 대상 디렉토리
targets:
  - src

# 검사 제외 디렉토리
exclude_dirs:
  - .venv
  - .git
  - tests
  - dist
  - build
  - htmlcov

# 최소 심각도 레벨: LOW | MEDIUM | HIGH
level: LOW

# 최소 신뢰도 레벨: LOW | MEDIUM | HIGH
confidence: LOW

# 비활성화할 규칙 ID
skips:
  - B101  # assert 구문 사용 허용 (테스트 코드 대응)
```

| 옵션             | 값                   | 설명                          |
|----------------|---------------------|-----------------------------|
| `targets`      | `[src]`             | 보안 검사를 수행할 디렉토리             |
| `exclude_dirs` | `.venv`, `tests` 등  | 검사에서 제외할 디렉토리               |
| `level`        | `LOW`               | 보고할 최소 심각도 (LOW = 모든 이슈 보고) |
| `confidence`   | `LOW`               | 보고할 최소 신뢰도 (LOW = 모든 이슈 보고) |
| `skips`        | `[B101]`            | 비활성화할 규칙 ID 목록              |

---

## 2. 심각도 & 신뢰도 레벨

### 심각도 (Severity)

| 레벨       | 의미                          |
|----------|-----------------------------|
| `HIGH`   | 즉시 수정이 필요한 심각한 취약점          |
| `MEDIUM` | 상황에 따라 위험할 수 있는 취약점         |
| `LOW`    | 잠재적 위험이 있지만 컨텍스트에 따라 허용 가능  |

### 신뢰도 (Confidence)

| 레벨       | 의미                    |
|----------|-----------------------|
| `HIGH`   | 취약점임이 거의 확실함          |
| `MEDIUM` | 취약점일 가능성이 높음          |
| `LOW`    | 취약점일 수도 있음 (오탐 가능성 있음) |

> 현재 설정: `level: LOW`, `confidence: LOW` → **모든 이슈를 빠짐없이 보고**

---

## 3. 주요 규칙 및 예제 코드

### B102 - exec 사용 금지

```python
# Bad [HIGH/MEDIUM] - 임의 코드 실행 위험
user_input = "import os; os.system('rm -rf /')"
exec(user_input)  # B102

# Good - exec 대신 명시적인 함수 호출 사용
def process():
    print("safe operation")

process()
```

### B103 - 파일 권한 설정

```python
import os

# Bad [MEDIUM/MEDIUM] - 지나치게 넓은 권한
os.chmod("/etc/config", 0o777)  # B103

# Good - 최소 권한 원칙 적용
os.chmod("/etc/config", 0o644)
```

### B105 / B106 / B107 - 하드코딩된 패스워드

```python
# Bad [LOW/MEDIUM] - 소스코드에 패스워드 직접 기재
password = "admin1234"        # B105
token = "secret-token-xyz"   # B105

def connect(password="admin1234"):  # B107
    pass

# Good - 환경변수에서 읽어오기
import os

password = os.environ.get("DB_PASSWORD")
token = os.environ.get("API_TOKEN")
```

### B110 - except pass (예외 무시)

```python
# Bad [LOW/HIGH] - 예외를 조용히 무시
try:
    risky_operation()
except Exception:
    pass  # B110

# Good - 예외를 로깅하거나 적절히 처리
import logging

try:
    risky_operation()
except Exception as e:
    logging.warning("작업 실패: %s", e)
```

### B303 / B324 - 취약한 해시 알고리즘 (MD5, SHA1)

```python
import hashlib

# Bad [MEDIUM/HIGH] - 보안 목적에 MD5/SHA1 사용은 취약
digest = hashlib.md5(data).hexdigest()   # B303
digest = hashlib.sha1(data).hexdigest()  # B303

# Good - SHA-256 이상 사용
digest = hashlib.sha256(data).hexdigest()
digest = hashlib.sha3_256(data).hexdigest()
```

### B307 - eval 사용 금지

```python
# Bad [MEDIUM/HIGH] - 임의 코드 실행 위험
user_expr = "1 + 1"
result = eval(user_expr)  # B307

# Good - 안전한 파싱 방식 사용 (리터럴 데이터만 처리)
import ast

data = ast.literal_eval("[1, 2, 3]")      # 리스트 리터럴 ✅
value = ast.literal_eval("{'key': 'val'}")  # 딕셔너리 리터럴 ✅
```

### B311 - 암호화 목적의 random 사용 금지

```python
import random

# Bad [LOW/HIGH] - random은 보안 목적으로 사용하면 안 됨
token = random.randint(100000, 999999)  # B311
session_id = random.choice("abcdefghijklmnopqrstuvwxyz")  # B311

# Good - secrets 모듈 사용
import secrets

token = secrets.randbelow(900000) + 100000
session_id = secrets.token_hex(16)
```

### B501 / B502 / B503 - SSL/TLS 검증 비활성화

```python
import ssl
import urllib.request

# Bad [HIGH/HIGH] - SSL 인증서 검증 비활성화
context = ssl.create_default_context()
context.check_hostname = False    # B502
context.verify_mode = ssl.CERT_NONE  # B501

# Good - 기본 SSL 검증 유지
context = ssl.create_default_context()
# check_hostname=True, verify_mode=CERT_REQUIRED (기본값)
```

### B602 / B605 - 쉘 인젝션

```python
import subprocess
import os

# Bad [HIGH/HIGH] - 사용자 입력이 shell 명령어에 그대로 삽입
filename = input("파일명: ")
os.system(f"cat {filename}")               # B605
subprocess.call(f"ls {filename}", shell=True)  # B602

# Good - shell=False 사용 + 인자 분리
subprocess.call(["ls", filename], shell=False)
```

### B608 - SQL 인젝션

```python
# Bad [MEDIUM/MEDIUM] - 문자열 포매팅으로 쿼리 조합
username = input("유저명: ")
query = f"SELECT * FROM users WHERE name = '{username}'"  # B608
cursor.execute(query)

# Good - 파라미터 바인딩 사용
query = "SELECT * FROM users WHERE name = ?"
cursor.execute(query, (username,))
```

### B701 / B702 - 템플릿 자동 이스케이프 비활성화 (XSS)

```python
# Bad [HIGH/HIGH] - Jinja2 자동 이스케이프 비활성화
from jinja2 import Environment

env = Environment(autoescape=False)  # B701

# Good - 자동 이스케이프 활성화
from jinja2 import Environment, select_autoescape

env = Environment(autoescape=select_autoescape(["html", "xml"]))
```

---

## 4. 비활성화 규칙

| 규칙    | 설명                      | 비활성화 이유                     |
|-------|-------------------------|------------------------------|
| `B101` | assert 구문 사용            | 테스트 코드에서 assert는 정상적인 사용 패턴 |

---

## 5. 실행 방법

```bash
# 기본 실행 (설정 파일 적용)
uv run bandit -c .bandit.yaml -r src

# 심각도 MEDIUM 이상만 보고
uv run bandit -c .bandit.yaml -r src -l

# 결과를 JSON으로 출력
uv run bandit -c .bandit.yaml -r src -f json -o bandit-report.json

# 특정 규칙만 검사
uv run bandit -r src -t B102,B307,B608
```

### 출력 예시

```
>> Issue: [B311:blacklist] Standard pseudo-random generators are not suitable for security/cryptographic purposes.
   Severity: LOW   Confidence: HIGH
   Location: src/auth.py:10
   More Info: https://bandit.readthedocs.io/en/latest/blacklists/blacklist_calls.html#b311-random
9	import random
10	token = random.randint(100000, 999999)
```