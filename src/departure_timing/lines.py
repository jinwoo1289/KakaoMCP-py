"""역·호선·방향 정규화 (Java SeoulArrivalClient에서 이식).

서비스가 외부 API를 부르기 전에 호선을 검증할 수 있도록 클라이언트에서 분리했다.
"""

import re

from departure_timing.errors import InvalidInputError

_SUBWAY_IDS: dict[str, str] = {
    "1": "1001", "1호선": "1001",
    "2": "1002", "2호선": "1002",
    "3": "1003", "3호선": "1003",
    "4": "1004", "4호선": "1004",
    "5": "1005", "5호선": "1005",
    "6": "1006", "6호선": "1006",
    "7": "1007", "7호선": "1007",
    "8": "1008", "8호선": "1008",
    "9": "1009", "9호선": "1009",
    "중앙선": "1061",
    "경의중앙선": "1063",
    "공항철도": "1065", "AREX": "1065",
    "경춘선": "1067",
    "수인분당선": "1075", "분당선": "1075", "수인선": "1075",
    "신분당선": "1077",
    "우이신설선": "1092",
    "서해선": "1093",
    "경강선": "1081",
    "GTX-A": "1032", "GTXA": "1032",
}


def subway_id_for(line: str | None) -> str | None:
    if line is None:
        return None
    return _SUBWAY_IDS.get(line.strip())


def normalize_station(station: str) -> str:
    return re.sub(r"\s+", "", station).replace("역", "")


def _is_line2(line: str | None) -> bool:
    return line is not None and line.strip() in ("2", "2호선")


def _line_name(line: str) -> str:
    name = line.strip()
    return f"{name}호선" if name.isdigit() else name


def _canonical_direction(direction: str) -> str:
    d = direction.strip()
    if d.lower() == "up":
        return "상행"
    if d.lower() == "down":
        return "하행"
    return d


def resolve_direction(direction: str | None, line: str | None) -> str | None:
    """API의 updnLine 값과 비교할 방향. None(생략)일 때만 방향 필터를 적용하지 않는다.

    2호선 실시간 데이터의 방향은 내선/외선이고 나머지 호선은 상행/하행이다.
    Java는 호선과 맞지 않거나 알 수 없는 값이면 필터를 조용히 꺼서 반대 방향 열차까지 섞어 판단했다.
    여기서는 입력 오류로 알리고, 서로 변환하지 않는다(2호선의 상행이 내선인지 외선인지는 역마다 다르다).
    """
    if direction is None or not direction.strip():
        return None
    d = _canonical_direction(direction)

    if _is_line2(line):
        if d in ("내선", "외선"):
            return d
        hint = "2호선은 방향을 내선 또는 외선으로 입력해 주세요."
        wrong_kind = d in ("상행", "하행")
    else:
        if d in ("상행", "하행"):
            return d
        hint = f"{_line_name(line or '')}은 방향을 상행 또는 하행으로 입력해 주세요."
        wrong_kind = d in ("내선", "외선")

    if wrong_kind:
        raise InvalidInputError(hint)
    raise InvalidInputError(f"알 수 없는 방향입니다: {direction.strip()}. {hint}")
