from collections.abc import Iterable


class StubArrivalClient:
    """미리 정한 도착 정보를 돌려주는 테스트·로컬 실행용 클라이언트."""

    def __init__(self, arrivals: Iterable[int] = ()) -> None:
        self.arrivals = list(arrivals)
        self.calls: list[tuple[str | None, str | None, str | None]] = []

    def get_remaining_minutes(
        self, station: str | None, line: str | None, direction: str | None
    ) -> list[int]:
        self.calls.append((station, line, direction))
        return list(self.arrivals)
