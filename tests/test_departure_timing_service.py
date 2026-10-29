"""Java DepartureTimingServiceTest(7개)의 이식 + 경계 사례 보강.

각 테스트 docstring의 이름은 대응하는 Java 테스트 메서드다.
"""

from datetime import time

import pytest

from departure_timing.clients import StubArrivalClient
from departure_timing.domain import AssessRequest, Decision, SavePresetRequest
from departure_timing.errors import ArrivalUnavailableError, InvalidInputError
from departure_timing.service import DepartureTimingService


@pytest.fixture
def stub_client() -> StubArrivalClient:
    return StubArrivalClient()


@pytest.fixture
def service(stub_client: StubArrivalClient) -> DepartureTimingService:
    return DepartureTimingService(stub_client)


def request(estimated: int | None, **kwargs) -> AssessRequest:
    fields = {"station": "서울역", "line": "1호선", "estimated_time_to_station": estimated}
    fields.update(kwargs)
    return AssessRequest(**fields)


# --- Java DepartureTimingServiceTest 이식 ---


def test_go_now_when_departing_now_means_no_wait(service, stub_client):
    """goNow_whenDepartingNowMeansNoWait"""
    stub_client.arrivals = [5]  # 5분 뒤 도착, buffer 1분
    res = service.assess(request(4))  # wait = 5-(4+1) = 0
    assert res.decision is Decision.GO_NOW


def test_wait_when_there_is_room_before_train_arrives(service, stub_client):
    """wait_whenThereIsRoomBeforeTrainArrives"""
    stub_client.arrivals = [10]
    res = service.assess(request(4))  # wait = 10-(4+1) = 5
    assert res.decision is Decision.WAIT
    assert res.recommended_departure_time is not None


def test_too_late_when_no_train_can_be_caught(service, stub_client):
    """tooLate_whenNoTrainCanBeCaught"""
    stub_client.arrivals = [2]  # wait = 2-(4+1) = -3 < 0
    res = service.assess(request(4))
    assert res.decision is Decision.TOO_LATE


def test_invalid_input_when_estimated_time_missing_and_no_preset(service, stub_client):
    """wait_whenEstimatedTimeMissingAndNoPreset

    Java는 WAIT를 돌려줬다. 하지만 WAIT는 "추천 시각에 출발하라"는 판단이고,
    이동 시간이 없으면 애초에 판단할 수 없다. 사용자 입력 문제이므로 InvalidInputError(REST 400)로 바꿨다.
    """
    stub_client.arrivals = [5]
    with pytest.raises(InvalidInputError, match="제공해주세요"):
        service.assess(request(None))


def test_arrival_unavailable_when_arrival_api_returns_empty(service, stub_client):
    """wait_whenArrivalApiReturnsEmpty

    Java는 WAIT를 돌려줬다. 도착 정보가 없으면 판단 근거가 없으므로
    정상 판단 결과 대신 ArrivalUnavailableError(REST 502, 판단 불가)로 바꿨다.
    """
    stub_client.arrivals = []
    with pytest.raises(ArrivalUnavailableError, match="실시간 열차 정보"):
        service.assess(request(4))


def test_save_preset_success(service):
    """savePreset_success"""
    res = service.save_preset(SavePresetRequest(preset_name="home", estimated_time_to_station=8))
    assert res.success is True


def test_save_preset_fails_when_name_is_blank(service):
    """savePreset_fails_whenNameIsBlank

    Java는 success=false를 HTTP 200으로 돌려줬다. assess와 같은 기준으로
    입력 오류는 InvalidInputError(REST 400)로 알리도록 바꿨다.
    """
    with pytest.raises(InvalidInputError):
        service.save_preset(SavePresetRequest(preset_name=" ", estimated_time_to_station=8))


# --- 보강: 판단 로직 (Java와 동일) ---


def test_wait_recommends_current_time_plus_station_wait(service, stub_client):
    stub_client.arrivals = [10]
    res = service.assess(request(4, current_time=time(8, 30)))
    assert res.recommended_departure_time == "08:35"
    assert res.reason == "지금 출발하면 역에서 약 5분 대기합니다. 08:35에 출발을 추천합니다."


def test_picks_first_catchable_train_skipping_earlier_ones(service, stub_client):
    stub_client.arrivals = [2, 5, 8]
    res = service.assess(request(4, current_time=time(8, 30)))  # 2 탈락, 5 → wait 0
    assert res.decision is Decision.GO_NOW
    assert res.recommended_departure_time == "NOW"


def test_recommended_time_wraps_past_midnight(service, stub_client):
    stub_client.arrivals = [10]
    res = service.assess(request(4, current_time=time(23, 58)))
    assert res.recommended_departure_time == "00:03"


def test_uses_injected_clock_when_current_time_absent(stub_client):
    service = DepartureTimingService(stub_client, now=lambda: time(7, 0))
    stub_client.arrivals = [10]
    assert service.assess(request(4)).recommended_departure_time == "07:05"


def test_preset_takes_priority_over_estimated_time(service, stub_client):
    service.save_preset(SavePresetRequest(preset_name="home", estimated_time_to_station=8))
    stub_client.arrivals = [9, 20]
    res = service.assess(request(1, preset_name="home"))  # 프리셋 8분 → 9-(8+1) = 0
    assert res.decision is Decision.GO_NOW


# --- 보강: 입력 오류 (Java와 다름) ---


def test_unknown_preset_is_invalid_even_with_estimated_time(service, stub_client):
    # Java는 없는 프리셋이면 조용히 estimatedTimeToStation으로 넘어갔다.
    # 이름을 지정했다면 오타일 가능성이 크므로 엉뚱한 이동 시간으로 판단하지 않는다.
    stub_client.arrivals = [5]
    with pytest.raises(InvalidInputError, match="nowhere"):
        service.assess(request(4, preset_name="nowhere"))


@pytest.mark.parametrize("estimated", [0, -3])
def test_non_positive_estimated_time_is_invalid(service, stub_client, estimated):
    stub_client.arrivals = [5]
    with pytest.raises(InvalidInputError):
        service.assess(request(estimated))


@pytest.mark.parametrize(
    "fields",
    [
        {"station": None},
        {"station": "  "},
        {"line": None},
        {"line": ""},
        {"line": "100호선"},
    ],
)
def test_invalid_station_or_line_is_rejected_before_calling_api(service, stub_client, fields):
    stub_client.arrivals = [5]
    with pytest.raises(InvalidInputError):
        service.assess(request(4, **fields))
    assert stub_client.calls == []


def test_input_error_wins_over_unavailable_api(service, stub_client):
    stub_client.arrivals = []  # API가 비어 있어도 입력 오류를 먼저 알린다
    with pytest.raises(InvalidInputError):
        service.assess(request(None))


def test_save_preset_fails_when_time_not_positive(service):
    with pytest.raises(InvalidInputError):
        service.save_preset(SavePresetRequest(preset_name="home", estimated_time_to_station=0))


# --- 보강: 호선별 방향 값 검증 (Java와 다름) ---
# Java는 호선과 맞지 않는 방향이면 방향 필터를 끄고 양방향 열차를 섞어 판단했다.


@pytest.mark.parametrize(
    ("line", "direction", "message"),
    [
        ("2호선", "상행", "2호선은 방향을 내선 또는 외선으로 입력해 주세요."),
        ("2", "down", "2호선은 방향을 내선 또는 외선으로 입력해 주세요."),
        ("7호선", "외선", "7호선은 방향을 상행 또는 하행으로 입력해 주세요."),
        ("7", "내선", "7호선은 방향을 상행 또는 하행으로 입력해 주세요."),
    ],
    ids=["line2-up", "line2-english-down", "line7-outer", "line7-number-inner"],
)
def test_direction_not_matching_line_is_invalid(service, stub_client, line, direction, message):
    stub_client.arrivals = [10]
    with pytest.raises(InvalidInputError) as excinfo:
        service.assess(request(4, line=line, direction=direction))
    assert str(excinfo.value) == message
    assert stub_client.calls == []  # 외부 API를 부르기 전에 막는다


@pytest.mark.parametrize(
    ("line", "direction"),
    [("2호선", "내선"), ("7호선", "하행"), ("2호선", None), ("7호선", None)],
    ids=["line2-inner", "line7-down", "line2-no-direction", "line7-no-direction"],
)
def test_direction_matching_line_is_assessed(service, stub_client, line, direction):
    stub_client.arrivals = [10]
    res = service.assess(request(4, line=line, direction=direction))
    assert res.decision is Decision.WAIT
    assert stub_client.calls == [("서울역", line, direction)]  # 변환 없이 그대로 전달


@pytest.mark.parametrize(
    ("line", "direction", "hint"),
    [
        ("2호선", "왼쪽", "2호선은 방향을 내선 또는 외선으로 입력해 주세요."),
        ("7호선", "강남 방면", "7호선은 방향을 상행 또는 하행으로 입력해 주세요."),
        ("7호선", "inner", "7호선은 방향을 상행 또는 하행으로 입력해 주세요."),
    ],
)
def test_unknown_direction_is_invalid(service, stub_client, line, direction, hint):
    # Java는 알 수 없는 방향이면 필터를 조용히 꺼서 양방향 열차를 섞어 판단했다.
    stub_client.arrivals = [10]
    with pytest.raises(InvalidInputError) as excinfo:
        service.assess(request(4, line=line, direction=direction))
    assert str(excinfo.value) == f"알 수 없는 방향입니다: {direction}. {hint}"
    assert stub_client.calls == []


def test_blank_direction_is_treated_as_omitted(service, stub_client):
    stub_client.arrivals = [10]
    assert service.assess(request(4, line="7호선", direction="  ")).decision is Decision.WAIT
