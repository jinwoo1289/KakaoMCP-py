"""MCP 도구 (공식 MCP Python SDK v2의 MCPServer).

도구 이름과 입력 스키마는 Java 버전(McpRequestDispatcher)과 같게 맞췄다.
판단은 REST와 같은 DepartureTimingService에 위임한다.

오류 매핑 (MCP 규격 2025-11-25 / 2026-07-28 Tools > Error Handling):
- 없는 도구 → 프로토콜 오류 JSON-RPC -32602
  (SDK 기본 동작은 isError 결과라서 규격에 맞게 바꾼다)
- 스키마 위반(필수값 누락, 타입 오류)과 도메인 입력 오류 → 도구 실행 오류 isError, "[입력 오류] ..."
- 외부 API 실패 → 도구 실행 오류 isError, "[판단 불가] ..." (원인별 고정 문구: 일시적/영구/열차 없음)
"""

import logging
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, CallToolResult, TextContent
from pydantic import Field, ValidationError

from departure_timing import domain
from departure_timing.errors import ArrivalUnavailableError, InvalidInputError
from departure_timing.schemas import AssessDepartureResponse, SavePresetResponse
from departure_timing.service import DepartureTimingService

logger = logging.getLogger(__name__)

SERVER_NAME = "departure-timing-mcp"
INPUT_ERROR_PREFIX = "[입력 오류]"
UNAVAILABLE_PREFIX = "[판단 불가]"


def _error_result(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)


def _validation_message(error: ValidationError) -> str:
    parts = []
    for err in error.errors():
        field = ".".join(str(p) for p in err["loc"])
        parts.append(f"{field}: {err['msg']}" if field else err["msg"])
    return "; ".join(parts)


def _minutes(value: float | None) -> int | None:
    """Java 스키마처럼 number로 받되, 소수점을 조용히 버리지 않는다(Java는 intValue()로 버렸다)."""
    if value is None:
        return None
    if not float(value).is_integer():
        raise InvalidInputError(f"estimatedTimeToStation은 정수(분)여야 합니다: {value}")
    return int(value)


class DepartureMCPServer(MCPServer):
    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context | None = None
    ) -> Any:
        if name not in {tool.name for tool in await self.list_tools()}:
            raise MCPError(INVALID_PARAMS, f"Unknown tool: {name}")

        try:
            return await super().call_tool(name, arguments, context)
        except ToolError as exc:
            cause = exc.__cause__
            if isinstance(cause, InvalidInputError):
                text = f"{INPUT_ERROR_PREFIX} {cause}"
                logger.warning("MCP tool %s failed: %s", name, text)
            elif isinstance(cause, ValidationError):
                text = f"{INPUT_ERROR_PREFIX} {_validation_message(cause)}"
                logger.warning("MCP tool %s failed: %s", name, text)
            elif isinstance(cause, ArrivalUnavailableError):
                # 세부 사유(오류 코드·외부 API 원문)는 로그에만 남기고, 결과에는 원인별 고정 문구만 쓴다.
                text = f"{UNAVAILABLE_PREFIX} {cause.public_message}"
                logger.warning("MCP tool %s arrival unavailable (%s): %s", name, cause.cause.value, cause)
            else:
                raise
            return _error_result(text)


def create_mcp_server(get_service: Callable[[], DepartureTimingService]) -> DepartureMCPServer:
    server = DepartureMCPServer(SERVER_NAME, version="0.1.0")

    # 매개변수 이름은 Java 도구 스키마의 camelCase 속성 이름 그대로다.
    @server.tool(
        name="assess_departure_timing",
        description="지하철 실시간 도착 정보를 바탕으로 최적 출발 시점을 판단합니다.",
    )
    def assess_departure_timing(
        station: Annotated[str, Field(description="출발하려는 지하철역 이름 (예: 서울역)")],
        line: Annotated[str, Field(description="지하철 노선 (예: 1호선)")],
        # Java에서는 선택이었지만, 생략하면 양방향 열차가 섞여 판단이 틀리므로 필수로 둔다.
        direction: Annotated[str, Field(description="상행/하행 (2호선은 내선/외선)")],
        estimatedTimeToStation: Annotated[
            float | None, Field(description="집에서 역까지 이동 시간(분)")
        ] = None,
        presetName: Annotated[str | None, Field(description="저장된 이동 시간 프리셋 이름")] = None,
    ) -> AssessDepartureResponse:
        result = get_service().assess(
            domain.AssessRequest(
                station=station,
                line=line,
                direction=direction,
                estimated_time_to_station=_minutes(estimatedTimeToStation),
                preset_name=presetName,
            )
        )
        return AssessDepartureResponse(
            decision=result.decision,
            recommended_departure_time=result.recommended_departure_time,
            reason=result.reason,
            recheck_after_minutes=result.recheck_after_minutes,
        )

    @server.tool(
        name="save_preset",
        description="집에서 역까지의 이동 시간을 프리셋으로 저장합니다.",
    )
    def save_preset(
        presetName: Annotated[str, Field(description="프리셋 이름")],
        estimatedTimeToStation: Annotated[float, Field(description="집에서 역까지 이동 시간(분)")],
    ) -> SavePresetResponse:
        result = get_service().save_preset(
            domain.SavePresetRequest(
                preset_name=presetName,
                estimated_time_to_station=_minutes(estimatedTimeToStation),
            )
        )
        return SavePresetResponse(success=result.success, message=result.message)

    return server
