"""REST 계층: 요청·응답 형식과 예외 → 상태 코드 매핑."""

import httpx
import pytest
from fastapi.testclient import TestClient

from departure_timing.api import create_app
from departure_timing.clients import SeoulArrivalClient, StubArrivalClient
from departure_timing.errors import ArrivalUnavailableError
from departure_timing.service import DepartureTimingService


class FailingArrivalClient:
    def get_remaining_minutes(self, station, line, direction):
        raise ArrivalUnavailableError("도착 정보 API에 연결하지 못했습니다.")


@pytest.fixture
def stub_client() -> StubArrivalClient:
    return StubArrivalClient([10])


@pytest.fixture
def api(stub_client) -> TestClient:
    return TestClient(create_app(DepartureTimingService(stub_client)))


ASSESS = {
    "station": "서울역",
    "line": "1호선",
    "direction": "상행",
    "estimatedTimeToStation": 4,
    "currentTime": "08:30",
}


def test_health(api):
    res = api.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_assess_returns_camel_case_decision(api):
    res = api.post("/departure/assess", json=ASSESS)
    assert res.status_code == 200
    assert res.json() == {
        "decision": "WAIT",
        "recommendedDepartureTime": "08:35",
        "reason": "지금 출발하면 역에서 약 5분 대기합니다. 08:35에 출발을 추천합니다.",
    }


def test_assess_go_now_and_too_late(api, stub_client):
    stub_client.arrivals = [5]
    assert api.post("/departure/assess", json=ASSESS).json()["recommendedDepartureTime"] == "NOW"
    stub_client.arrivals = [2]
    body = api.post("/departure/assess", json=ASSESS).json()
    assert body["decision"] == "TOO_LATE"
    assert body["recommendedDepartureTime"] is None


def test_saved_preset_is_used_by_assess(api):
    res = api.post("/presets", json={"presetName": "home", "estimatedTimeToStation": 9})
    assert res.status_code == 200
    assert res.json() == {"success": True, "message": "프리셋 'home' 저장 완료"}

    payload = {**ASSESS, "presetName": "home", "estimatedTimeToStation": None}
    assert api.post("/departure/assess", json=payload).json()["decision"] == "GO_NOW"


@pytest.mark.parametrize(
    "override",
    [
        {"station": None},
        {"line": "100호선"},
        {"estimatedTimeToStation": None},
        {"estimatedTimeToStation": 0},
        {"presetName": "nowhere"},
        {"estimatedTimeToStation": "abc"},
        {"currentTime": "25:99"},
    ],
)
def test_assess_input_errors_are_400(api, override):
    res = api.post("/departure/assess", json={**ASSESS, **override})
    assert res.status_code == 400
    assert res.json()["error"] == "INVALID_INPUT"
    assert res.json()["message"]


@pytest.mark.parametrize(
    "content",
    [b"{", '{"station":"신대방역"}'.encode("cp949")],
    ids=["malformed-json", "non-utf8"],
)
def test_unreadable_body_is_400_in_same_format(api, content):
    res = api.post("/departure/assess", content=content, headers={"content-type": "application/json"})
    assert res.status_code == 400
    assert res.json()["error"] == "INVALID_INPUT"


def test_unknown_path_keeps_default_404(api):
    assert api.get("/nope").status_code == 404


@pytest.mark.parametrize(
    "payload",
    [{"presetName": " ", "estimatedTimeToStation": 5}, {"presetName": "home"}],
)
def test_save_preset_input_errors_are_400(api, payload):
    res = api.post("/presets", json=payload)
    assert res.status_code == 400
    assert res.json()["error"] == "INVALID_INPUT"


def test_arrival_unavailable_is_502():
    api = TestClient(create_app(DepartureTimingService(FailingArrivalClient())))
    res = api.post("/departure/assess", json=ASSESS)
    assert res.status_code == 502
    assert res.json()["error"] == "ARRIVAL_UNAVAILABLE"
    assert "decision" not in res.json()


def test_retries_exhausted_end_to_end_is_502():
    attempts = 0

    def always_503(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503)

    client = SeoulArrivalClient(
        "KEY", httpx.Client(transport=httpx.MockTransport(always_503)), sleep=lambda _: None
    )
    api = TestClient(create_app(DepartureTimingService(client)))

    res = api.post("/departure/assess", json=ASSESS)
    assert res.status_code == 502
    assert "3회 시도" in res.json()["message"]
    assert attempts == 3


def test_startup_requires_api_key(monkeypatch):
    monkeypatch.delenv("SEOUL_SUBWAY_APIKEY", raising=False)
    with pytest.raises(RuntimeError, match="SEOUL_SUBWAY_APIKEY"):
        with TestClient(create_app()):
            pass


def test_startup_with_api_key_wires_real_client(monkeypatch):
    monkeypatch.setenv("SEOUL_SUBWAY_APIKEY", "test-key")
    with TestClient(create_app()) as api:
        assert api.get("/health").status_code == 200
        assert isinstance(api.app.state.service, DepartureTimingService)


def test_direction_not_matching_line_is_400(api):
    res = api.post("/departure/assess", json={**ASSESS, "line": "2호선", "direction": "상행"})
    assert res.status_code == 400
    assert res.json() == {
        "error": "INVALID_INPUT",
        "message": "2호선은 방향을 내선 또는 외선으로 입력해 주세요.",
    }


def test_unknown_direction_is_400(api):
    res = api.post("/departure/assess", json={**ASSESS, "direction": "왼쪽"})
    assert res.status_code == 400
    assert res.json()["message"].startswith("알 수 없는 방향입니다: 왼쪽.")


def test_omitted_direction_is_400(api):
    payload = {key: value for key, value in ASSESS.items() if key != "direction"}
    res = api.post("/departure/assess", json=payload)
    assert res.status_code == 400
    assert res.json()["message"] == (
        "방향(direction)이 필요합니다. 1호선은 방향을 상행 또는 하행으로 입력해 주세요."
    )
