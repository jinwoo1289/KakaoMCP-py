"""REST 요청·응답 모델. JSON 필드 이름은 Java 버전과 같은 camelCase를 쓴다."""

from datetime import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from departure_timing.domain import Decision


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class AssessDepartureRequest(CamelModel):
    station: str | None = Field(None, description="지하철역 이름", examples=["신대방역"])
    line: str | None = Field(None, description="호선", examples=["2호선"])
    # station·line처럼 필수지만, 누락 시 호선별 안내가 담긴 도메인 오류 메시지를 주려고 모델에서는 선택으로 둔다.
    direction: str | None = Field(
        None, description="필수. 상행/하행 (2호선은 내선/외선)", examples=["내선"]
    )
    estimated_time_to_station: int | None = Field(
        None, description="역까지 이동 시간(분)", examples=[10]
    )
    preset_name: str | None = Field(
        None, description="저장된 이동 시간 프리셋 이름. 있으면 estimatedTimeToStation보다 우선"
    )
    current_time: time | None = Field(
        None, description="현재 시각(HH:mm). 없으면 서버 기준 한국 시각", examples=["08:30"]
    )


class AssessDepartureResponse(CamelModel):
    decision: Decision
    recommended_departure_time: str | None = Field(
        description="HH:mm, 바로 출발이면 NOW, TOO_LATE면 null"
    )
    reason: str
    recheck_after_minutes: int | None = Field(
        None, description="TOO_LATE일 때만: 첫 열차 도착까지 남은 분. 이 시간 뒤에 다시 확인. 그 밖에는 null"
    )


class SavePresetRequest(CamelModel):
    preset_name: str | None = Field(None, examples=["home"])
    estimated_time_to_station: int | None = Field(None, examples=[10])


class SavePresetResponse(CamelModel):
    success: bool
    message: str


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ErrorResponse(BaseModel):
    error: Literal["INVALID_INPUT"]
    message: str


class UnavailableErrorResponse(BaseModel):
    error: Literal["ARRIVAL_UNAVAILABLE"]
    message: str
    retryable: bool = Field(description="같은 요청을 잠시 후 다시 보내면 해결될 수 있는지")
