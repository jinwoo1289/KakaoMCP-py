"""MCP 도구: SDK의 메모리 내 클라이언트로 도구 목록, 판단, 오류 매핑을 확인한다."""

import json
from datetime import time

import pytest
from fastapi.testclient import TestClient
from mcp.client.client import Client
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS

from departure_timing.api import create_app
from departure_timing.clients import StubArrivalClient
from departure_timing.errors import ArrivalUnavailableError
from departure_timing.mcp_server import create_mcp_server
from departure_timing.service import DepartureTimingService

pytestmark = pytest.mark.anyio

# Java McpRequestDispatcher.toolsListResult()의 스키마 (이름 → (속성별 (타입, 설명), required))
JAVA_TOOLS = {
    "assess_departure_timing": (
        {
            "station": ("string", "출발하려는 지하철역 이름 (예: 서울역)"),
            "line": ("string", "지하철 노선 (예: 1호선)"),
            "estimatedTimeToStation": ("number", "집에서 역까지 이동 시간(분)"),
            # 의도적으로 Java("상행/하행")와 다르다: 2호선 데이터는 내선/외선이라
            # LLM이 2호선에 상행/하행을 넣지 않도록 설명을 보완했다.
            "direction": ("string", "상행/하행 (2호선은 내선/외선)"),
            "presetName": ("string", "저장된 이동 시간 프리셋 이름"),
        },
        # 의도적으로 Java(["station", "line"])와 다르다: 방향을 생략하면 양방향 열차가 섞여 판단이 틀리므로 필수로 바꿨다.
        ["station", "line", "direction"],
    ),
    "save_preset": (
        {
            "presetName": ("string", "프리셋 이름"),
            "estimatedTimeToStation": ("number", "집에서 역까지 이동 시간(분)"),
        },
        ["presetName", "estimatedTimeToStation"],
    ),
}

ASSESS = {"station": "서울역", "line": "1호선", "direction": "상행", "estimatedTimeToStation": 4}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def stub_client() -> StubArrivalClient:
    return StubArrivalClient([10])


@pytest.fixture
def service(stub_client) -> DepartureTimingService:
    return DepartureTimingService(stub_client, now=lambda: time(8, 30))


@pytest.fixture
async def mcp(service):
    async with Client(create_mcp_server(lambda: service)) as client:
        yield client


class FailingArrivalClient:
    def get_remaining_minutes(self, station, line, direction):
        raise ArrivalUnavailableError("도착 정보 API 호출에 실패했습니다 (3회 시도, 마지막 사유: HTTP 503).")


def text_of(result) -> str:
    return result.content[0].text


def property_type(schema: dict) -> str:
    """선택 속성은 SDK가 anyOf [T, null]로 표현하므로 null이 아닌 타입을 꺼낸다."""
    if "type" in schema:
        return schema["type"]
    return next(s["type"] for s in schema["anyOf"] if s["type"] != "null")


# --- tools/list ---


async def test_lists_both_tools_with_java_compatible_schema(mcp):
    tools = {tool.name: tool for tool in (await mcp.list_tools()).tools}
    assert set(tools) == set(JAVA_TOOLS)

    for name, (properties, required) in JAVA_TOOLS.items():
        schema = tools[name].input_schema
        assert sorted(schema["required"]) == sorted(required)
        assert set(schema["properties"]) == set(properties)
        for prop, (type_, description) in properties.items():
            assert property_type(schema["properties"][prop]) == type_, prop
            assert schema["properties"][prop]["description"] == description, prop


# --- 정상 판단 ---


async def test_assess_returns_decision_as_text_and_structured_content(mcp):
    result = await mcp.call_tool("assess_departure_timing", ASSESS)
    expected = {
        "decision": "WAIT",
        "recommendedDepartureTime": "08:35",
        "reason": "지금 출발하면 역에서 약 5분 대기합니다. 08:35에 출발을 추천합니다.",
    }
    assert result.is_error is False
    assert result.structured_content == expected
    assert json.loads(text_of(result)) == expected


async def test_save_preset_then_assess_with_preset(mcp):
    saved = await mcp.call_tool("save_preset", {"presetName": "home", "estimatedTimeToStation": 9})
    assert saved.structured_content == {"success": True, "message": "프리셋 'home' 저장 완료"}

    result = await mcp.call_tool(
        "assess_departure_timing",
        {"station": "서울역", "line": "1호선", "direction": "상행", "presetName": "home"},
    )
    assert result.structured_content["decision"] == "GO_NOW"


async def test_integral_float_minutes_are_accepted(mcp):
    result = await mcp.call_tool("assess_departure_timing", {**ASSESS, "estimatedTimeToStation": 4.0})
    assert result.structured_content["decision"] == "WAIT"


# --- 도구 실행 오류: 입력 오류 ---


@pytest.mark.parametrize(
    ("tool", "arguments", "reason"),
    [
        ("assess_departure_timing", {**ASSESS, "line": "100호선"}, "지원하지 않는 호선"),
        ("assess_departure_timing", {**ASSESS, "presetName": "nowhere"}, "nowhere"),
        ("assess_departure_timing", {**ASSESS, "estimatedTimeToStation": 0}, "estimatedTimeToStation"),
        ("assess_departure_timing", {**ASSESS, "station": " "}, "station"),
        ("assess_departure_timing", {**ASSESS, "estimatedTimeToStation": 7.5}, "정수"),
        ("assess_departure_timing", {**ASSESS, "line": "2호선", "direction": "상행"}, "내선 또는 외선"),
        ("assess_departure_timing", {**ASSESS, "line": "7호선", "direction": "외선"}, "상행 또는 하행"),
        ("assess_departure_timing", {**ASSESS, "direction": "왼쪽"}, "알 수 없는 방향"),
        ("save_preset", {"presetName": "home", "estimatedTimeToStation": -1}, "1분 이상"),
    ],
    ids=["unknown-line", "unknown-preset", "zero-minutes", "blank-station", "fractional", "line2-up", "line7-outer", "unknown-direction", "preset-minutes"],
)
async def test_domain_input_errors_are_tool_errors(mcp, tool, arguments, reason):
    result = await mcp.call_tool(tool, arguments)
    assert result.is_error is True
    assert text_of(result).startswith("[입력 오류] ")
    assert reason in text_of(result)


@pytest.mark.parametrize(
    ("arguments", "field"),
    [
        ({"station": "서울역"}, "line"),
        ({"station": "서울역", "line": "2호선"}, "direction"),
        ({**ASSESS, "estimatedTimeToStation": "abc"}, "estimatedTimeToStation"),
    ],
    ids=["missing-required", "missing-direction", "wrong-type"],
)
async def test_schema_violations_are_tool_errors_per_spec(mcp, arguments, field):
    # MCP 규격은 입력 검증 오류를 도구 실행 오류로 분류한다(모델이 고쳐서 다시 시도할 수 있도록).
    result = await mcp.call_tool("assess_departure_timing", arguments)
    assert result.is_error is True
    assert text_of(result).startswith(f"[입력 오류] {field}")


# --- 도구 실행 오류: 판단 불가 ---


async def test_arrival_unavailable_is_tool_error():
    service = DepartureTimingService(FailingArrivalClient())
    async with Client(create_mcp_server(lambda: service)) as mcp:
        result = await mcp.call_tool("assess_departure_timing", ASSESS)

    assert result.is_error is True
    assert text_of(result).startswith("[판단 불가] ")
    assert text_of(result).endswith("잠시 후 다시 시도해 주세요.")
    assert result.structured_content is None


# --- 프로토콜 오류 ---


async def test_unknown_tool_is_protocol_error(mcp):
    with pytest.raises(MCPError) as excinfo:
        await mcp.call_tool("no_such_tool", {})
    assert excinfo.value.error.code == INVALID_PARAMS
    assert "no_such_tool" in excinfo.value.error.message


# --- HTTP 마운트 (/mcp, Streamable HTTP) ---

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}


def rpc(method: str, params: dict, id_: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params}


def initialize() -> dict:
    return rpc(
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
    )


@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
def test_http_endpoint_serves_mcp_without_redirect(service, path):
    with TestClient(create_app(service, mcp_allowed_hosts=["testserver"])) as api:
        init = api.post(path, json=initialize(), headers=MCP_HEADERS, follow_redirects=False)
        assert init.status_code == 200
        assert init.json()["result"]["serverInfo"]["name"] == "departure-timing-mcp"

        call = api.post(
            path,
            json=rpc("tools/call", {"name": "assess_departure_timing", "arguments": ASSESS}, 2),
            headers=MCP_HEADERS,
            follow_redirects=False,
        )
        assert call.status_code == 200
        assert call.json()["result"]["structuredContent"]["decision"] == "WAIT"


def test_http_unknown_tool_is_jsonrpc_error(service):
    with TestClient(create_app(service, mcp_allowed_hosts=["testserver"])) as api:
        res = api.post(
            "/mcp", json=rpc("tools/call", {"name": "nope", "arguments": {}}), headers=MCP_HEADERS
        )
        assert res.json()["error"]["code"] == -32602


def test_presets_are_shared_between_rest_and_mcp(service):
    with TestClient(create_app(service, mcp_allowed_hosts=["testserver"])) as api:
        api.post("/presets", json={"presetName": "home", "estimatedTimeToStation": 9})
        res = api.post(
            "/mcp",
            json=rpc(
                "tools/call",
                {
                    "name": "assess_departure_timing",
                    "arguments": {"station": "서울역", "line": "1", "direction": "상행", "presetName": "home"},
                },
            ),
            headers=MCP_HEADERS,
        )
        assert res.json()["result"]["structuredContent"]["decision"] == "GO_NOW"


def test_http_rejects_host_not_allowed(service, monkeypatch):
    # 허용 호스트를 지정하지 않으면 SDK 기본값(localhost만 허용)이 적용된다.
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    with TestClient(create_app(service)) as api:
        res = api.post("/mcp", json=initialize(), headers=MCP_HEADERS)
        assert res.status_code >= 400
        assert api.get("/health").status_code == 200  # REST는 영향 없음
