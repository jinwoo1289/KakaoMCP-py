"""FastAPI REST 엔드포인트 + /mcp (MCP Streamable HTTP).

실행: uvicorn departure_timing.api:create_app --factory
프리셋은 프로세스 메모리에 있으므로 워커는 1개로 띄운다.
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, time
from zoneinfo import ZoneInfo

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mcp.server.transport_security import TransportSecuritySettings
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from departure_timing import domain
from departure_timing.clients.seoul import SeoulArrivalClient
from departure_timing.config import Settings
from departure_timing.errors import ArrivalUnavailableError, InvalidInputError
from departure_timing.mcp_server import create_mcp_server
from departure_timing.schemas import (
    AssessDepartureRequest,
    AssessDepartureResponse,
    ErrorResponse,
    HealthResponse,
    SavePresetRequest,
    SavePresetResponse,
    UnavailableErrorResponse,
)
from departure_timing.service import DepartureTimingService

KST = ZoneInfo("Asia/Seoul")
MCP_PATH = "/mcp"
ERROR_RESPONSES = {400: {"model": ErrorResponse}, 502: {"model": UnavailableErrorResponse}}


def kst_now() -> time:
    return datetime.now(KST).time()


def _configure_logging() -> None:
    # MCPServer가 생성될 때 루트 로거에 메시지만 찍는 핸들러를 달기 때문에 force로 교체한다.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    # httpx는 INFO로 요청 URL을 남기는데, 서울시 API는 키가 URL 경로에 들어간다.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _error(status: int, code: str, message: str, **extra: object) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message, **extra})


def _validation_message(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors():
        field = ".".join(str(p) for p in err["loc"] if p != "body")
        parts.append(f"{field}: {err['msg']}" if field else err["msg"])
    return "; ".join(parts) or "요청 형식이 올바르지 않습니다."


def get_service(request: Request) -> DepartureTimingService:
    return request.app.state.service


def _mcp_transport_security(allowed_hosts: list[str] | None) -> TransportSecuritySettings | None:
    """None이면 SDK 기본값(localhost Host만 허용하는 DNS rebinding 보호)을 쓴다."""
    if allowed_hosts is None:
        raw = os.environ.get("MCP_ALLOWED_HOSTS", "")
        allowed_hosts = [h.strip() for h in raw.split(",") if h.strip()] or None
    if allowed_hosts is None:
        return None
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=[f"{scheme}://{h}" for h in allowed_hosts for scheme in ("http", "https")],
    )


class _McpPathAlias:
    """/mcp 마운트는 /mcp/만 받고 /mcp는 307로 돌린다. POST 리다이렉트를 따르지 않는 클라이언트를 위해 /mcp도 직접 받는다."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] == MCP_PATH:
            scope = {**scope, "path": MCP_PATH + "/", "raw_path": (MCP_PATH + "/").encode()}
        await self.app(scope, receive, send)


def create_app(
    service: DepartureTimingService | None = None,
    mcp_allowed_hosts: list[str] | None = None,
) -> FastAPI:
    """service를 주면 그대로 쓰고(테스트), 없으면 환경변수로 실제 서울시 API 클라이언트를 만든다.

    mcp_allowed_hosts가 없으면 환경변수 MCP_ALLOWED_HOSTS(쉼표 구분)를 쓴다.
    """
    # REST와 MCP가 같은 서비스(같은 프리셋 저장소)를 쓴다.
    mcp_server = create_mcp_server(lambda: app.state.service)
    mcp_app = mcp_server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=_mcp_transport_security(mcp_allowed_hosts),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if service is not None:
            async with mcp_server.session_manager.run():
                yield
            return
        _configure_logging()
        # 설정 오류는 MCP 세션 매니저(TaskGroup)를 띄우기 전에 그대로 드러낸다.
        settings = Settings.from_env()
        with httpx.Client() as http:
            client = SeoulArrivalClient(
                settings.seoul_api_key, http, base_url=settings.seoul_base_url
            )
            app.state.service = DepartureTimingService(client, now=kst_now)
            async with mcp_server.session_manager.run():
                yield

    app = FastAPI(title="Departure Timing", version="0.1.0", lifespan=lifespan)
    if service is not None:
        app.state.service = service
    app.mount(MCP_PATH, mcp_app)
    app.add_middleware(_McpPathAlias)

    @app.exception_handler(InvalidInputError)
    async def invalid_input(request: Request, exc: InvalidInputError) -> JSONResponse:
        return _error(400, "INVALID_INPUT", str(exc))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error(400, "INVALID_INPUT", _validation_message(exc))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # UTF-8이 아닌 본문 등은 FastAPI가 HTTPException(400)으로 던진다. 400만 같은 형식으로 맞춘다.
        if exc.status_code == 400:
            return _error(400, "INVALID_INPUT", str(exc.detail))
        return await http_exception_handler(request, exc)

    @app.exception_handler(ArrivalUnavailableError)
    async def arrival_unavailable(request: Request, exc: ArrivalUnavailableError) -> JSONResponse:
        # 세부 사유(오류 코드·외부 API 원문)는 클라이언트 로그에만 남기고 응답에는 고정 문구만 쓴다.
        return _error(502, "ARRIVAL_UNAVAILABLE", exc.public_message, retryable=exc.retryable)

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse()

    @app.post(
        "/departure/assess", response_model=AssessDepartureResponse, responses=ERROR_RESPONSES
    )
    def assess(
        body: AssessDepartureRequest, service: DepartureTimingService = Depends(get_service)
    ) -> AssessDepartureResponse:
        result = service.assess(domain.AssessRequest(**body.model_dump()))
        return AssessDepartureResponse(
            decision=result.decision,
            recommended_departure_time=result.recommended_departure_time,
            reason=result.reason,
            recheck_after_minutes=result.recheck_after_minutes,
            unknown_arrival_count=result.unknown_arrival_count,
        )

    @app.post("/presets", response_model=SavePresetResponse, responses={400: {"model": ErrorResponse}})
    def save_preset(
        body: SavePresetRequest, service: DepartureTimingService = Depends(get_service)
    ) -> SavePresetResponse:
        result = service.save_preset(domain.SavePresetRequest(**body.model_dump()))
        return SavePresetResponse(success=result.success, message=result.message)

    return app
