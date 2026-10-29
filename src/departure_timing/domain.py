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
class AssessResult:
    decision: Decision
    recommended_departure_time: str | None
    reason: str
    # TOO_LATE일 때만: 도착 목록의 첫 열차가 도착하기까지 남은 분. 그 뒤에 목록이 갱신된다.
    recheck_after_minutes: int | None = None


@dataclass(frozen=True)
class SavePresetRequest:
    preset_name: str | None = None
    estimated_time_to_station: int | None = None


@dataclass(frozen=True)
class SavePresetResult:
    success: bool
    message: str
