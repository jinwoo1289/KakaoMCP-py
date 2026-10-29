from typing import Protocol

from departure_timing.domain import Arrivals


class ArrivalClient(Protocol):
    def get_arrivals(
        self, station: str | None, line: str | None, direction: str | None
    ) -> Arrivals:
        """해당 역·호선·방향에 도착 예정인 열차 정보를 돌려준다.

        minutes는 도착 시간이 알려진 열차들의 남은 시간(분, 오름차순)이고,
        도착 시간이 제공되지 않은 열차는 unknown_count로만 센다.
        판정할 수 있는 열차가 없으면 빈 목록이나 임의의 값 대신 ArrivalUnavailableError를 던진다.
        """
        ...
