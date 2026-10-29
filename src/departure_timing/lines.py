"""역·호선·방향 정규화 (Java SeoulArrivalClient에서 이식).

서비스가 외부 API를 부르기 전에 호선을 검증할 수 있도록 클라이언트에서 분리했다.
"""

import re

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


def normalize_direction(direction: str | None, line: str | None) -> str | None:
    """API의 updnLine 값과 비교할 방향. None이면 방향 필터를 적용하지 않는다."""
    if direction is None:
        return None
    d = direction.strip()

    if d.lower() == "up":
        d = "상행"
    if d.lower() == "down":
        d = "하행"

    # 2호선은 상/하행을 내/외선으로 바꿀 수 없다(역마다 다름).
    # 내선/외선이면 그대로 쓰고, 상행/하행이면 방향 필터를 적용하지 않는다.
    if _is_line2(line):
        return d if d in ("내선", "외선") else None

    return d if d in ("상행", "하행") else None
