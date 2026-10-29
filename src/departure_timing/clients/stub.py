from collections.abc import Iterable

from departure_timing.domain import Arrivals


class StubArrivalClient:
    """미리 정한 도착 정보를 돌려주는 테스트·로컬 실행용 클라이언트."""

    def __init__(self, arrivals: Iterable[int] = (), unknown_count: int = 0) -> None:
        self.arrivals = list(arrivals)
        self.unknown_count = unknown_count
        self.calls: list[tuple[str | None, str | None, str | None]] = []

    def get_arrivals(
        self, station: str | None, line: str | None, direction: str | None
    ) -> Arrivals:
        self.calls.append((station, line, direction))
        return Arrivals(list(self.arrivals), self.unknown_count)
