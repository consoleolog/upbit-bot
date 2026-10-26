import datetime
import json
from dataclasses import dataclass
from enum import Enum

import pytest

from src.models.base import Base

# ---------------------------------------------------------------------------
# 테스트용 픽스처 모델
# ---------------------------------------------------------------------------


class Color(Enum):
    RED = "red"
    BLUE = "blue"


@dataclass
class Address(Base):
    city: str
    zip_code: str


@dataclass
class Person(Base):
    name: str
    age: int
    color: Color
    birthday: datetime.date
    created_at: datetime.datetime
    address: Address
    tags: list
    nickname: str | None = None


@dataclass
class Simple(Base):
    value: str


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_to_dict_primitive_fields():
    """기본 타입 필드가 그대로 반환된다."""
    obj = Simple(value="hello")
    assert obj.to_dict() == {"value": "hello"}


@pytest.mark.unit
def test_to_dict_none_field():
    """None 필드는 None으로 반환된다."""
    person = _make_person(nickname=None)
    result = person.to_dict()
    assert result["nickname"] is None


@pytest.mark.unit
def test_to_dict_enum_converted_to_value():
    """Enum 필드는 .value(문자열)로 변환된다."""
    person = _make_person()
    result = person.to_dict()
    assert result["color"] == "red"


@pytest.mark.unit
def test_to_dict_datetime_converted_to_isoformat():
    """datetime 필드는 ISO 8601 문자열로 변환된다."""
    dt = datetime.datetime(2024, 1, 15, 9, 30, 0)
    person = _make_person(created_at=dt)
    result = person.to_dict()
    assert result["created_at"] == "2024-01-15T09:30:00"


@pytest.mark.unit
def test_to_dict_date_converted_to_isoformat():
    """date 필드는 ISO 8601 문자열로 변환된다."""
    person = _make_person(birthday=datetime.date(1990, 5, 20))
    result = person.to_dict()
    assert result["birthday"] == "1990-05-20"


@pytest.mark.unit
def test_to_dict_nested_base_calls_to_dict():
    """중첩된 Base 객체는 재귀적으로 to_dict()가 호출된다."""
    person = _make_person()
    result = person.to_dict()
    assert result["address"] == {"city": "Seoul", "zip_code": "04524"}


@pytest.mark.unit
def test_to_dict_list_of_primitives():
    """list 내 기본 타입은 그대로 반환된다."""
    person = _make_person(tags=["python", "trading"])
    result = person.to_dict()
    assert result["tags"] == ["python", "trading"]


@pytest.mark.unit
def test_to_dict_list_of_base_objects():
    """list 내 Base 객체는 to_dict()가 재귀 호출된다."""
    person = _make_person(tags=[Simple(value="a"), Simple(value="b")])
    result = person.to_dict()
    assert result["tags"] == [{"value": "a"}, {"value": "b"}]


# ---------------------------------------------------------------------------
# from_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_from_dict_primitive_fields():
    """기본 타입 필드가 올바르게 매핑된다."""
    obj = Simple.from_dict({"value": "hello"})
    assert obj.value == "hello"


@pytest.mark.unit
def test_from_dict_none_field():
    """None 필드는 None으로 유지된다."""
    person = Person.from_dict(_make_dict(nickname=None))
    assert person.nickname is None


@pytest.mark.unit
def test_from_dict_enum_from_string():
    """문자열 값이 Enum으로 변환된다."""
    person = Person.from_dict(_make_dict(color="blue"))
    assert person.color is Color.BLUE


@pytest.mark.unit
def test_from_dict_enum_already_enum():
    """이미 Enum인 값은 그대로 유지된다."""
    person = Person.from_dict(_make_dict(color=Color.RED))
    assert person.color is Color.RED


@pytest.mark.unit
def test_from_dict_datetime_from_string():
    """ISO 문자열이 datetime으로 변환된다."""
    person = Person.from_dict(_make_dict(created_at="2024-06-01T12:00:00"))
    assert person.created_at == datetime.datetime(2024, 6, 1, 12, 0, 0)


@pytest.mark.unit
def test_from_dict_datetime_already_datetime():
    """이미 datetime인 값은 그대로 유지된다."""
    dt = datetime.datetime(2024, 6, 1, 12, 0, 0)
    person = Person.from_dict(_make_dict(created_at=dt))
    assert person.created_at == dt


@pytest.mark.unit
def test_from_dict_date_from_string():
    """ISO 문자열이 date로 변환된다."""
    person = Person.from_dict(_make_dict(birthday="1995-03-10"))
    assert person.birthday == datetime.date(1995, 3, 10)


@pytest.mark.unit
def test_from_dict_date_already_date():
    """이미 date인 값은 그대로 유지된다."""
    d = datetime.date(1995, 3, 10)
    person = Person.from_dict(_make_dict(birthday=d))
    assert person.birthday == d


@pytest.mark.unit
def test_from_dict_nested_base():
    """중첩된 Base 타입 필드는 from_dict()가 재귀 호출된다."""
    person = Person.from_dict(_make_dict(address={"city": "Busan", "zip_code": "48000"}))
    assert isinstance(person.address, Address)
    assert person.address.city == "Busan"


@pytest.mark.unit
def test_from_dict_does_not_mutate_input():
    """from_dict()는 입력 딕셔너리를 수정하지 않는다."""
    data = _make_dict()
    original = dict(data)
    Person.from_dict(data)
    assert data == original


# ---------------------------------------------------------------------------
# to_json / from_json
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_to_json_returns_valid_json():
    """to_json()이 파싱 가능한 JSON 문자열을 반환한다."""
    obj = Simple(value="test")
    result = obj.to_json()
    parsed = json.loads(result)
    assert parsed == {"value": "test"}


@pytest.mark.unit
def test_from_json_roundtrip():
    """to_json() → from_json() 왕복 변환이 동일한 값을 반환한다."""
    obj = Simple(value="roundtrip")
    restored = Simple.from_json(obj.to_json())
    assert restored.value == obj.value


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _make_person(**overrides) -> Person:
    defaults = {
        "name": "Alice",
        "age": 30,
        "color": Color.RED,
        "birthday": datetime.date(1994, 7, 1),
        "created_at": datetime.datetime(2024, 1, 1, 0, 0, 0),
        "address": Address(city="Seoul", zip_code="04524"),
        "tags": ["tag1"],
        "nickname": None,
    }
    return Person(**{**defaults, **overrides})


def _make_dict(**overrides) -> dict:
    defaults = {
        "name": "Alice",
        "age": 30,
        "color": "red",
        "birthday": "1994-07-01",
        "created_at": "2024-01-01T00:00:00",
        "address": {"city": "Seoul", "zip_code": "04524"},
        "tags": ["tag1"],
        "nickname": None,
    }
    return {**defaults, **overrides}
