"""FastAPI REST 엔드포인트.

실행: uvicorn departure_timing.api:create_app --factory
프리셋은 프로세스 메모리에 있으므로 워커는 1개로 띄운다.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, time
from zoneinfo import ZoneInfo

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from departure_timing import domain
from departure_timing.clients.seoul import SeoulArrivalClient
from departure_timing.config import Settings
from departure_timing.errors import ArrivalUnavailableError, InvalidInputError
from departure_timing.schemas import (
    AssessDepartureRequest,
    AssessDepartureResponse,
    ErrorResponse,
    HealthResponse,
    SavePresetRequest,
    SavePresetResponse,
)
from departure_timing.service import DepartureTimingService

KST = ZoneInfo("Asia/Seoul")
ERROR_RESPONSES = {400: {"model": ErrorResponse}, 502: {"model": ErrorResponse}}


def kst_now() -> time:
    return datetime.now(KST).time()


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # httpx는 INFO로 요청 URL을 남기는데, 서울시 API는 키가 URL 경로에 들어간다.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message})


def _validation_message(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors():
        field = ".".join(str(p) for p in err["loc"] if p != "body")
        parts.append(f"{field}: {err['msg']}" if field else err["msg"])
    return "; ".join(parts) or "요청 형식이 올바르지 않습니다."


def get_service(request: Request) -> DepartureTimingService:
    return request.app.state.service


def create_app(service: DepartureTimingService | None = None) -> FastAPI:
    """service를 주면 그대로 쓰고(테스트), 없으면 환경변수로 실제 서울시 API 클라이언트를 만든다."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if service is not None:
            yield
            return
        _configure_logging()
        settings = Settings.from_env()
        with httpx.Client() as http:
            client = SeoulArrivalClient(
                settings.seoul_api_key, http, base_url=settings.seoul_base_url
            )
            app.state.service = DepartureTimingService(client, now=kst_now)
            yield

    app = FastAPI(title="Departure Timing", version="0.1.0", lifespan=lifespan)
    if service is not None:
        app.state.service = service

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
        return _error(502, "ARRIVAL_UNAVAILABLE", f"판단할 수 없습니다: {exc}")

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
        )

    @app.post("/presets", response_model=SavePresetResponse, responses={400: {"model": ErrorResponse}})
    def save_preset(
        body: SavePresetRequest, service: DepartureTimingService = Depends(get_service)
    ) -> SavePresetResponse:
        result = service.save_preset(domain.SavePresetRequest(**body.model_dump()))
        return SavePresetResponse(success=result.success, message=result.message)

    return app
