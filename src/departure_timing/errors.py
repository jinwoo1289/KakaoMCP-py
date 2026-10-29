"""판단 결과(GO_NOW/WAIT/TOO_LATE)와 구분되는 실패.

판단을 내릴 수 없는 상황을 정상 결과로 포장하지 않기 위해 예외로 분리한다.
"""


class DepartureTimingError(Exception):
    pass


class InvalidInputError(DepartureTimingError):
    """사용자 입력이 잘못되어 판단할 수 없다 (REST 400)."""


class ArrivalUnavailableError(DepartureTimingError):
    """실시간 도착 정보를 얻지 못해 판단할 수 없다 (REST 502)."""
