"""판단 로직이 주고받는 값 객체."""

from dataclasses import dataclass
from datetime import time
from enum import Enum


class Decision(str, Enum):
    GO_NOW = "GO_NOW"
    WAIT = "WAIT"
    TOO_LATE = "TOO_LATE"


@dataclass(frozen=True)
class AssessRequest:
    station: str | None = None
    line: str | None = None
    direction: str | None = None
    estimated_time_to_station: int | None = None
    preset_name: str | None = None
    current_time: time | None = None


@dataclass(frozen=True)
class Arrivals:
    # 도착 시간이 알려진 열차들의 남은 분(오름차순). 0은 지금 진입·도착 중인 열차.
    minutes: list[int]
    # 도착 시간이 제공되지 않은 열차 수. 판정에서는 제외하지만 결과에서 숨기지 않는다.
    unknown_count: int = 0


@dataclass(frozen=True)
class AssessResult:
    decision: Decision
    recommended_departure_time: str | None
    reason: str
    # TOO_LATE일 때만: 도착 목록의 첫 열차가 도착하기까지 남은 분. 그 뒤에 목록이 갱신된다.
    recheck_after_minutes: int | None = None
    unknown_arrival_count: int = 0


@dataclass(frozen=True)
class SavePresetRequest:
    preset_name: str | None = None
    estimated_time_to_station: int | None = None


@dataclass(frozen=True)
class SavePresetResult:
    success: bool
    message: str
