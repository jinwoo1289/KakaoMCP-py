"""Java DepartureTimingServiceTest(7개)의 이식 + 경계 사례 보강.

각 테스트 docstring의 이름은 대응하는 Java 테스트 메서드다.
"""

from datetime import time

import pytest

from departure_timing.clients import StubArrivalClient
from departure_timing.domain import AssessRequest, Decision, SavePresetRequest
from departure_timing.service import DepartureTimingService


@pytest.fixture
def stub_client() -> StubArrivalClient:
    return StubArrivalClient()


@pytest.fixture
def service(stub_client: StubArrivalClient) -> DepartureTimingService:
    return DepartureTimingService(stub_client)


def request(estimated: int | None, **kwargs) -> AssessRequest:
    return AssessRequest(
        station="서울역", line="1호선", estimated_time_to_station=estimated, **kwargs
    )


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


def test_wait_when_estimated_time_missing_and_no_preset(service, stub_client):
    """wait_whenEstimatedTimeMissingAndNoPreset"""
    stub_client.arrivals = [5]
    res = service.assess(request(None))
    assert res.decision is Decision.WAIT
    assert "제공해주세요" in res.reason


def test_wait_when_arrival_api_returns_empty(service, stub_client):
    """wait_whenArrivalApiReturnsEmpty"""
    stub_client.arrivals = []
    res = service.assess(request(4))
    assert res.decision is Decision.WAIT
    assert "실시간 열차 정보" in res.reason


def test_save_preset_success(service):
    """savePreset_success"""
    res = service.save_preset(SavePresetRequest(preset_name="home", estimated_time_to_station=8))
    assert res.success is True


def test_save_preset_fails_when_name_is_blank(service):
    """savePreset_fails_whenNameIsBlank"""
    res = service.save_preset(SavePresetRequest(preset_name=" ", estimated_time_to_station=8))
    assert res.success is False


# --- 보강: Java 코드에서 읽어낸 동작을 고정 ---


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


def test_unknown_preset_falls_back_to_estimated_time(service, stub_client):
    stub_client.arrivals = [5]
    res = service.assess(request(4, preset_name="nowhere"))
    assert res.decision is Decision.GO_NOW


def test_save_preset_fails_when_time_not_positive(service):
    res = service.save_preset(SavePresetRequest(preset_name="home", estimated_time_to_station=0))
    assert res.success is False
