"""Java DepartureTimingService의 판단 로직 이식."""

from collections.abc import Callable
from datetime import datetime, time

from departure_timing.clients.base import ArrivalClient
from departure_timing.domain import (
    AssessRequest,
    AssessResult,
    Decision,
    SavePresetRequest,
    SavePresetResult,
)

BUFFER_MINUTES = 1


def _local_now() -> time:
    return datetime.now().time()


def _plus_minutes(t: time, minutes: int) -> str:
    """LocalTime.plusMinutes(...).format("HH:mm")와 같이 자정을 넘기면 00시로 돌아간다."""
    total = (t.hour * 60 + t.minute + minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _is_blank(value: str | None) -> bool:
    return value is None or not value.strip()


class DepartureTimingService:
    def __init__(
        self, arrival_client: ArrivalClient, now: Callable[[], time] = _local_now
    ) -> None:
        self._arrival_client = arrival_client
        self._now = now
        self._presets: dict[str, int] = {}

    def assess(self, request: AssessRequest) -> AssessResult:
        arrivals = self._arrival_client.get_remaining_minutes(
            request.station, request.line, request.direction
        )

        if not arrivals:
            return AssessResult(
                Decision.WAIT,
                None,
                "실시간 열차 정보를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.",
            )

        estimated: int | None = None
        if not _is_blank(request.preset_name):
            estimated = self._presets.get(request.preset_name)
        if estimated is None:
            estimated = request.estimated_time_to_station

        if estimated is None or estimated <= 0:
            return AssessResult(
                Decision.WAIT,
                None,
                "presetName 또는 estimatedTimeToStation(1 이상)을 제공해주세요.",
            )

        station_wait: int | None = None
        for arrival_min in arrivals:
            wait = arrival_min - (estimated + BUFFER_MINUTES)
            if wait >= 0:
                station_wait = wait
                break

        current_time = request.current_time or self._now()

        if station_wait is None:
            return AssessResult(
                Decision.TOO_LATE,
                None,
                "현재 도착 예정인 열차들로는 탑승이 어렵습니다. "
                "다음 열차 정보 갱신을 위해 잠시 후에 다시 시도해 주세요",
            )
        if station_wait == 0:
            return AssessResult(
                Decision.GO_NOW,
                "NOW",
                "지금 출발하면 역에서 대기 없이 바로 탑승할 수 있습니다.",
            )

        recommended = _plus_minutes(current_time, station_wait)
        return AssessResult(
            Decision.WAIT,
            recommended,
            f"지금 출발하면 역에서 약 {station_wait}분 대기합니다. "
            f"{recommended}에 출발을 추천합니다.",
        )

    def save_preset(self, request: SavePresetRequest) -> SavePresetResult:
        if _is_blank(request.preset_name):
            return SavePresetResult(False, "프리셋 이름을 입력해주세요.")
        if request.estimated_time_to_station is None or request.estimated_time_to_station <= 0:
            return SavePresetResult(False, "1분 이상의 시간을 입력해 주세요.")

        self._presets[request.preset_name] = request.estimated_time_to_station
        return SavePresetResult(True, f"프리셋 '{request.preset_name}' 저장 완료")
