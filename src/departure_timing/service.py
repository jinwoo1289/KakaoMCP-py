"""Java DepartureTimingService의 판단 로직 이식.

판단 규칙(버퍼, 열차 선택, GO_NOW/WAIT/TOO_LATE)은 Java와 같다.
Java와 달리 입력 오류와 도착 정보 부재는 판단 결과가 아니라 예외로 알린다.
"""

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
from departure_timing.errors import ArrivalUnavailableError, InvalidInputError
from departure_timing.lines import direction_mismatch, subway_id_for

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
        # 입력 검증을 외부 호출보다 먼저 한다: API 장애와 무관하게 입력 오류는 입력 오류로 알린다.
        if _is_blank(request.station):
            raise InvalidInputError("station(역 이름)을 입력해 주세요.")
        if _is_blank(request.line):
            raise InvalidInputError("line(호선)을 입력해 주세요.")
        if subway_id_for(request.line) is None:
            raise InvalidInputError(f"지원하지 않는 호선입니다: {request.line}")
        # 호선과 맞지 않는 방향은 클라이언트가 방향 필터를 끄고 양방향 열차를 섞어 판단하게 되므로 막는다.
        mismatch = direction_mismatch(request.direction, request.line)
        if mismatch:
            raise InvalidInputError(mismatch)
        estimated = self._resolve_estimated_time(request)

        arrivals = self._arrival_client.get_remaining_minutes(
            request.station, request.line, request.direction
        )
        if not arrivals:
            # 클라이언트는 빈 목록 대신 ArrivalUnavailableError를 던지기로 약속하지만,
            # 어떤 구현이든 빈 목록으로 판단 결과를 만들지 않도록 여기서도 막는다.
            raise ArrivalUnavailableError("실시간 열차 정보를 불러오지 못했습니다.")

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

    def _resolve_estimated_time(self, request: AssessRequest) -> int:
        if not _is_blank(request.preset_name):
            # 이름을 지정했는데 없으면 오타일 가능성이 크므로 estimatedTimeToStation으로 넘어가지 않는다.
            if request.preset_name not in self._presets:
                raise InvalidInputError(f"저장된 프리셋 '{request.preset_name}'이(가) 없습니다.")
            return self._presets[request.preset_name]

        estimated = request.estimated_time_to_station
        if estimated is None or estimated <= 0:
            raise InvalidInputError("presetName 또는 estimatedTimeToStation(1 이상)을 제공해주세요.")
        return estimated

    def save_preset(self, request: SavePresetRequest) -> SavePresetResult:
        if _is_blank(request.preset_name):
            raise InvalidInputError("프리셋 이름을 입력해주세요.")
        if request.estimated_time_to_station is None or request.estimated_time_to_station <= 0:
            raise InvalidInputError("1분 이상의 시간을 입력해 주세요.")

        self._presets[request.preset_name] = request.estimated_time_to_station
        return SavePresetResult(True, f"프리셋 '{request.preset_name}' 저장 완료")
