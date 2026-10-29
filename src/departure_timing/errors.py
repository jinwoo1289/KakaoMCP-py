"""판단 결과(GO_NOW/WAIT/TOO_LATE)와 구분되는 실패.

판단을 내릴 수 없는 상황을 정상 결과로 포장하지 않기 위해 예외로 분리한다.
"""

from enum import Enum


class DepartureTimingError(Exception):
    pass


class InvalidInputError(DepartureTimingError):
    """사용자 입력이 잘못되어 판단할 수 없다 (REST 400)."""


class UnavailableCause(str, Enum):
    TRANSIENT = "transient"  # 서버 무응답·장애: 잠시 후 다시 시도하면 될 수 있다
    PERMANENT = "permanent"  # 키 오류·호출 한도·요청 형식 등 설정 문제: 다시 시도해도 같다
    NO_TRAINS = "no_trains"  # 정상 응답이지만 해당 호선·방향 열차가 없다


_PUBLIC_MESSAGES = {
    UnavailableCause.TRANSIENT: "실시간 정보 서버가 응답하지 않습니다. 잠시 후 다시 시도해 주세요.",
    UnavailableCause.PERMANENT: "실시간 정보 서비스 설정 문제로 조회할 수 없습니다. 다시 시도해도 해결되지 않습니다.",
    UnavailableCause.NO_TRAINS: "현재 운행 중인 열차 정보가 없습니다.",
}


class ArrivalUnavailableError(DepartureTimingError):
    """실시간 도착 정보를 얻지 못해 판단할 수 없다 (REST 502).

    str(e)는 로그용 세부 사유(오류 코드, 외부 API 원문 메시지)이고,
    사용자에게는 원인별 고정 문구(public_message)만 보여준다.
    """

    def __init__(self, detail: str, cause: UnavailableCause) -> None:
        super().__init__(detail)
        self.cause = cause

    @property
    def retryable(self) -> bool:
        return self.cause is UnavailableCause.TRANSIENT

    @property
    def public_message(self) -> str:
        return _PUBLIC_MESSAGES[self.cause]
