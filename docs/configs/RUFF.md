# Ruff 설정 가이드

> **Ruff**: Rust로 작성된 Python 린터 및 포매터. 기존 flake8, isort, black 등을 대체합니다.

- 공식 문서: https://docs.astral.sh/ruff/configuration/
- 설정 파일: `.ruff.toml` (프로젝트 루트)

---

## 목차

1. [기본 설정](#1-기본-설정)
2. [lint - 린트 규칙](#2-lint---린트-규칙)
3. [lint.isort - import 정렬](#3-lintisort---import-정렬)
4. [format - 코드 포매팅](#4-format---코드-포매팅)

---

## 1. 기본 설정

```toml
line-length = 120
target-version = "py310"
```

| 옵션               | 값         | 설명                          |
|------------------|-----------|-----------------------------|
| `line-length`    | `120`     | 한 줄 최대 길이 (formatter 기준)    |
| `target-version` | `"py310"` | 대상 Python 버전 (버전에 맞는 규칙 적용) |

---

## 2. lint - 린트 규칙

### 활성화 규칙 (`select`)

```toml
[lint]
select = [
    "E", # pycodestyle errors
    "W", # pycodestyle warnings
    "F", # pyflakes
    "I", # isort
    "N", # pep8-naming
    "UP", # pyupgrade
    "B", # flake8-bugbear
    "C4", # flake8-comprehensions
    "SIM", # flake8-simplify
    "RUF", # ruff-specific rules
]
```

| 코드    | 설명                    | 예시 규칙                        |
|-------|-----------------------|------------------------------|
| `E`   | pycodestyle 에러        | 들여쓰기, 공백 오류 등                |
| `W`   | pycodestyle 경고        | 불필요한 공백, 줄 끝 공백 등            |
| `F`   | pyflakes              | 미사용 import, 미정의 변수 등         |
| `I`   | isort                 | import 순서 정렬                 |
| `N`   | pep8-naming           | 변수/함수/클래스 네이밍 컨벤션            |
| `UP`  | pyupgrade             | 구버전 Python 문법을 최신 문법으로 자동 대체 |
| `B`   | flake8-bugbear        | 잠재적 버그 및 안티패턴 감지             |
| `C4`  | flake8-comprehensions | 불필요한 comprehension 단순화       |
| `SIM` | flake8-simplify       | 단순화 가능한 코드 감지                |
| `RUF` | ruff-specific rules   | Ruff 자체 규칙                   |

### 예제 코드

**F - pyflakes: 미사용 import 감지**

```python
# Bad
import os
import sys  # 사용하지 않음

print(os.getcwd())

# Good
import os

print(os.getcwd())
```

**N - pep8-naming: 네이밍 컨벤션**

```python
# Bad
def MyFunction():  # 함수는 snake_case여야 함
    pass


class my_class:  # 클래스는 PascalCase여야 함
    pass


# Good
def my_function():
    pass


class MyClass:
    pass
```

**UP - pyupgrade: 최신 문법으로 대체**

```python
# Bad (구버전 방식)
from typing import List, Dict, Optional


def get_items() -> Optional[List[Dict[str, int]]]:
    return None


# Good (Python 3.10+ 방식)
def get_items() -> list[dict[str, int]] | None:
    return None
```

**B - flake8-bugbear: 잠재적 버그 감지**

```python
# Bad (B006: mutable default argument)
def append_item(item, items=[]):
    items.append(item)
    return items


# Good
def append_item(item, items=None):
    if items is None:
        items = []
    items.append(item)
    return items
```

**C4 - flake8-comprehensions: comprehension 단순화**

```python
# Bad
result = list([x for x in range(10)])  # 불필요한 list() 감싸기
squares = dict([(x, x ** 2) for x in range(5)])

# Good
result = [x for x in range(10)]
squares = {x: x ** 2 for x in range(5)}
```

**SIM - flake8-simplify: 코드 단순화**

```python
# Bad
if condition == True:
    pass

if x is not None:
    return x
else:
    return default

# Good
if condition:
    pass

return x if x is not None else default
```

### 비활성화 규칙 (`ignore`)

```toml
ignore = [
    "E501", # line-length는 formatter에 위임
]
```

| 코드     | 이유                                                |
|--------|---------------------------------------------------|
| `E501` | 줄 길이 초과 경고를 lint가 아닌 formatter(`line-length`)에 위임 |

---

## 3. lint.isort - import 정렬

```toml
[lint.isort]
known-first-party = ["src"]
```

| 옵션                  | 값         | 설명                          |
|---------------------|-----------|-----------------------------|
| `known-first-party` | `["src"]` | `src/` 하위 모듈을 1st-party로 인식 |

isort는 import를 아래 세 그룹으로 분류하여 정렬합니다:

1. **Standard library** (표준 라이브러리): `os`, `sys`, `typing` 등
2. **Third-party** (외부 패키지): `requests`, `fastapi` 등
3. **First-party** (프로젝트 내부): `src.*`

### 예제 코드

```python
# Bad (정렬 안 된 상태)
from src.utils import helper
import os
import requests
from src.models import User
import sys

# Good (isort 적용 후)
import os
import sys

import requests

from src.models import User
from src.utils import helper
```

---

## 4. format - 코드 포매팅

```toml
[format]
quote-style = "double"
indent-style = "space"
line-ending = "auto"
skip-magic-trailing-comma = true
```

| 옵션                          | 값          | 설명                              |
|-----------------------------|------------|---------------------------------|
| `quote-style`               | `"double"` | 문자열 따옴표를 큰따옴표(`"`)로 통일          |
| `indent-style`              | `"space"`  | 들여쓰기를 탭이 아닌 공백으로                |
| `line-ending`               | `"auto"`   | OS에 맞는 줄바꿈 문자 자동 적용             |
| `skip-magic-trailing-comma` | `true`     | trailing comma가 있어도 강제 줄바꿈하지 않음 |

### 예제 코드

**quote-style: 따옴표 통일**

```python
# Before
message = 'Hello, World!'
name = 'Python'

# After (double quote로 통일)
message = "Hello, World!"
name = "Python"
```

**skip-magic-trailing-comma: trailing comma 처리**

```python
# skip-magic-trailing-comma = false (기본값)
# trailing comma가 있으면 강제로 줄바꿈
items = [
    "apple",
    "banana",
    "cherry",  # trailing comma → 각 항목을 별도 줄로 유지
]

# skip-magic-trailing-comma = true (현재 설정)
# trailing comma가 있어도 한 줄이 가능하면 한 줄로 포매팅
items = ["apple", "banana", "cherry"]
```

---

## 실행 방법

```bash
# 린트 검사
uv run ruff check .

# 린트 검사 + 자동 수정
uv run ruff check --fix .

# 코드 포매팅
uv run ruff format .

# 린트 + 포매팅 한 번에
uv run ruff check --fix . && uv run ruff format .
```
