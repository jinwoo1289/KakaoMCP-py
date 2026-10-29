"""SeoulArrivalClient 재시도: 일시적 실패만, 최대 3회, 시간 예산 안에서."""

import logging

import httpx
import pytest

from departure_timing.clients import RetryPolicy, SeoulArrivalClient
from departure_timing.errors import ArrivalUnavailableError

OK_BODY = (
    "<realtimeStationArrival><RESULT><code>INFO-000</code></RESULT>"
    "<row><subwayId>1001</subwayId><updnLine>상행</updnLine><barvlDt>120</barvlDt></row>"
    "</realtimeStationArrival>"
)
EMPTY_BODY = "<realtimeStationArrival><RESULT><code>INFO-000</code></RESULT></realtimeStationArrival>"


class FakeClock:
    """sleep과 요청 소요 시간을 실제로 기다리지 않고 시계만 진행시킨다."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class ScriptedServer:
    """정해 둔 순서대로 실패(예외 또는 상태 코드)를 돌려준 뒤 성공하는 Stub 서버."""

    def __init__(self, script, clock: FakeClock, request_seconds: float = 0.0) -> None:
        self.script = list(script)
        self.clock = clock
        self.request_seconds = request_seconds
        self.attempts = 0
        self.timeouts: list[float] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.attempts += 1
        self.timeouts.append(request.extensions["timeout"]["read"])
        self.clock.now += self.request_seconds
        step = self.script.pop(0) if self.script else 200
        if isinstance(step, Exception):
            raise step
        return httpx.Response(step, text=OK_BODY if step == 200 else "")


def make_client(server: ScriptedServer, clock: FakeClock, **policy) -> SeoulArrivalClient:
    return SeoulArrivalClient(
        "KEY",
        httpx.Client(transport=httpx.MockTransport(server)),
        retry=RetryPolicy(**policy),
        sleep=clock.sleep,
        clock=clock,
        jitter=lambda: 0.0,
    )


def fetch(client: SeoulArrivalClient) -> list[int]:
    return client.get_remaining_minutes("서울역", "1호선", "상행")


@pytest.mark.parametrize(
    "failures",
    [
        [httpx.ReadTimeout("slow")],
        [httpx.ConnectError("refused"), 503],
        [500, 429],
    ],
    ids=["timeout", "connect-then-503", "500-then-429"],
)
def test_succeeds_after_transient_failures(failures, caplog):
    clock = FakeClock()
    server = ScriptedServer(failures, clock)

    with caplog.at_level(logging.WARNING):
        assert fetch(make_client(server, clock)) == [2]

    assert server.attempts == len(failures) + 1
    assert clock.sleeps == [0.3, 0.6][: len(failures)]
    failure_logs = [r.getMessage() for r in caplog.records if "failed" in r.getMessage()]
    assert failure_logs[0].startswith("seoul arrival API attempt 1/3 failed:")
    assert len(failure_logs) == len(failures)


def test_gives_up_after_three_attempts_as_unavailable():
    clock = FakeClock()
    server = ScriptedServer([503, 503, 503, 503], clock)

    with pytest.raises(ArrivalUnavailableError, match="3회 시도.*HTTP 503"):
        fetch(make_client(server, clock))

    assert server.attempts == 3
    assert clock.sleeps == [0.3, 0.6]


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_does_not_retry_4xx(status):
    clock = FakeClock()
    server = ScriptedServer([status], clock)

    with pytest.raises(ArrivalUnavailableError, match=str(status)):
        fetch(make_client(server, clock))

    assert server.attempts == 1
    assert clock.sleeps == []


def test_does_not_retry_empty_response():
    clock = FakeClock()
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, text=EMPTY_BODY)

    client = SeoulArrivalClient(
        "KEY", httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=clock.sleep, clock=clock,
    )
    with pytest.raises(ArrivalUnavailableError):
        fetch(client)

    assert attempts == 1
    assert clock.sleeps == []


def test_stops_retrying_when_time_budget_is_exhausted():
    clock = FakeClock()
    # 매 시도가 1.5초(요청 타임아웃) 걸려 실패: 1.5 + 0.3 + 1.5 = 3.3초 > 예산 3초
    server = ScriptedServer([httpx.ReadTimeout("slow")] * 3, clock, request_seconds=1.5)

    with pytest.raises(ArrivalUnavailableError, match="2회 시도"):
        fetch(make_client(server, clock))

    assert server.attempts == 2


def test_request_timeout_never_exceeds_remaining_budget():
    clock = FakeClock()
    server = ScriptedServer([httpx.ReadTimeout("slow")] * 2, clock, request_seconds=1.0)

    fetch(make_client(server, clock, request_timeout=1.5, total_budget=3.0))

    # 시도 3은 t=1.0+0.3+1.0+0.6=2.9초에 시작하므로 남은 예산 0.1초만 쓴다
    assert server.timeouts == [1.5, 1.5, pytest.approx(0.1)]


def test_backoff_adds_jitter():
    policy = RetryPolicy()
    assert policy.backoff(1, jitter=0.0) == pytest.approx(0.3)
    assert policy.backoff(2, jitter=1.0) == pytest.approx(0.7)
