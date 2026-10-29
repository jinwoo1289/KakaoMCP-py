from typing import Protocol


class ArrivalClient(Protocol):
    def get_remaining_minutes(
        self, station: str | None, line: str | None, direction: str | None
    ) -> list[int]:
        """해당 역·호선·방향에 도착 예정인 열차들의 남은 시간(분)을 오름차순으로 돌려준다.

        도착 정보를 얻지 못하면 빈 목록이나 임의의 값 대신 ArrivalUnavailableError를 던진다.
        """
        ...
