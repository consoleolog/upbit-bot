import datetime
import json
from dataclasses import dataclass, fields
from enum import Enum
from typing import Any, Self, cast, get_type_hints


@dataclass
class Base:
    """dataclass 기반 모델의 공통 직렬화/역직렬화를 제공하는 기반 클래스.

    지원하는 필드 타입:
        - 기본 타입 (str, int, float, bool)
        - None
        - Enum → value 문자열로 직렬화, 역직렬화 시 Enum으로 복원
        - datetime.datetime / datetime.date → ISO 8601 문자열로 직렬화
        - 중첩 Base 객체 → 재귀적으로 직렬화/역직렬화
        - list → 요소별 재귀 처리
    """

    def to_dict(self) -> dict:
        """인스턴스를 직렬화 가능한 딕셔너리로 변환한다.

        Returns:
            필드명을 키로, 직렬화된 값을 값으로 하는 딕셔너리.
        """
        data = {}
        for f in fields(cast(Any, self)):
            value = getattr(self, f.name)
            if value is None:
                data[f.name] = value
            elif hasattr(value, "to_dict"):
                data[f.name] = value.to_dict()
            elif isinstance(value, Enum):
                data[f.name] = value.value
            elif isinstance(value, datetime.datetime | datetime.date):
                data[f.name] = value.isoformat()
            elif isinstance(value, list):
                data[f.name] = [item.to_dict() if hasattr(item, "to_dict") else item for item in value]
            elif isinstance(value, dict):
                data[f.name] = {k: v.to_dict() if hasattr(v, "to_dict") else v for k, v in value.items()}
            else:
                data[f.name] = value
        return data

    def to_json(self, **kwargs) -> str:
        """인스턴스를 JSON 문자열로 직렬화한다.

        Args:
            **kwargs: json.dumps 에 전달할 추가 인수.

        Returns:
            JSON 직렬화 문자열.
        """
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """딕셔너리를 해당 클래스의 인스턴스로 역직렬화한다.

        입력 딕셔너리를 수정하지 않으며, 타입 힌트를 기반으로
        Enum · datetime · 중첩 Base 필드를 자동 변환한다.

        Args:
            data: 필드명을 키로 하는 딕셔너리.

        Returns:
            역직렬화된 클래스 인스턴스.
        """
        _missing = object()
        hints = get_type_hints(cls)
        kwargs = {}
        for f in fields(cast(Any, cls)):
            raw = data.get(f.name, _missing)
            if raw is _missing:
                continue  # 키 없음 → dataclass 기본값 사용
            value = raw
            field_type = hints.get(f.name)
            if value is None:
                kwargs[f.name] = value
            elif hasattr(field_type, "from_dict"):
                kwargs[f.name] = field_type.from_dict(value)
            elif field_type is datetime.datetime:
                if isinstance(value, str):
                    try:
                        kwargs[f.name] = datetime.datetime.fromisoformat(value)
                    except ValueError:
                        kwargs[f.name] = value  # 비ISO 포맷은 __post_init__에서 처리
                else:
                    kwargs[f.name] = value
            elif field_type is datetime.date:
                kwargs[f.name] = datetime.date.fromisoformat(value) if isinstance(value, str) else value
            elif isinstance(field_type, type) and issubclass(field_type, Enum):
                kwargs[f.name] = field_type(value) if not isinstance(value, field_type) else value
            else:
                kwargs[f.name] = value
        return cast(Any, cls)(**kwargs)

    @classmethod
    def from_json(cls, json_str: str, **kwargs) -> Self:
        """JSON 문자열을 해당 클래스의 인스턴스로 역직렬화한다.

        Args:
            json_str: JSON 직렬화 문자열.
            **kwargs: json.loads 에 전달할 추가 인수.

        Returns:
            역직렬화된 클래스 인스턴스.
        """
        data = json.loads(json_str, **kwargs)
        return cls.from_dict(data)
