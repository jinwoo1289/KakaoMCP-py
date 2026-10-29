"""SeoulArrivalClient: 응답 해석은 Java와 같고, 실패하면 가짜 도착 정보 대신 예외를 던진다."""

from urllib.parse import unquote

import httpx
import pytest

from departure_timing.clients import SeoulArrivalClient
from departure_timing.clients.seoul import classify_api_code
from departure_timing.errors import ArrivalUnavailableError, InvalidInputError, UnavailableCause

OK_RESULT = "<RESULT><code>INFO-000</code><message>정상 처리되었습니다.</message></RESULT>"


def row(subway_id: str, updn_line: str, barvl_dt: str) -> str:
    return (
        f"<row><subwayId>{subway_id}</subwayId><updnLine>{updn_line}</updnLine>"
        f"<barvlDt>{barvl_dt}</barvlDt></row>"
    )


def body(*rows: str, result: str = OK_RESULT) -> str:
    return f"<realtimeStationArrival>{result}{''.join(rows)}</realtimeStationArrival>"


def client_with(handler) -> SeoulArrivalClient:
    return SeoulArrivalClient(
        "KEY", httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None
    )


def responding(text: str, status: int = 200):
    return lambda request: httpx.Response(status, text=text)


def test_filters_by_line_and_direction_and_converts_seconds_to_minutes():
    client = client_with(responding(body(
        row("1001", "상행", "61"),    # 2분 (올림)
        row("1001", "하행", "120"),   # 방향 다름
        row("1002", "상행", "30"),    # 호선 다름
        row("1001", "상행", "0"),     # 0분은 제외 (Java와 동일)
        row("1001", "상행", "abc"),   # 숫자 아님은 제외
        row("1001", "상행", "300"),   # 5분
    )))
    assert client.get_remaining_minutes("서울역", "1호선", "상행") == [2, 5]


def test_keeps_at_most_five_sorted_arrivals():
    rows = [row("1001", "상행", str(sec)) for sec in (600, 60, 540, 120, 480, 180, 420)]
    client = client_with(responding(body(*rows)))
    assert client.get_remaining_minutes("서울", "1", "상행") == [1, 2, 3, 7, 8]


def test_line2_filters_by_inner_outer_direction():
    client = client_with(responding(body(row("1002", "내선", "120"), row("1002", "외선", "240"))))
    assert client.get_remaining_minutes("신대방", "2호선", "외선") == [4]
    assert client.get_remaining_minutes("신대방", "2호선", "내선") == [2]


@pytest.mark.parametrize(
    ("line", "direction"),
    [
        ("2호선", None),
        ("2호선", "상행"),
        ("2호선", "왼쪽"),
        ("7호선", None),
        ("7호선", "외선"),
        ("7호선", "강남 방면"),
    ],
)
def test_omitted_wrong_or_unknown_direction_raises_before_calling_api(line, direction):
    # Java(및 이 클라이언트의 이전 버전)는 방향이 없거나 이런 값이면 방향 필터를 조용히 꺼서
    # 반대 방향 열차까지 섞인 목록을 돌려줬다. 이제는 API를 부르기 전에 입력 오류로 막는다.
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=body(row("1002", "내선", "120")))

    with pytest.raises(InvalidInputError):
        client_with(handler).get_remaining_minutes("신대방", line, direction)
    assert requests == []


def test_request_url_uses_normalized_station():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(unquote(request.url.path))
        return httpx.Response(200, text=body(row("1001", "상행", "120")))

    client_with(handler).get_remaining_minutes("서울 역", "1호선", "상행")
    assert seen == ["/api/subway/KEY/xml/realtimeStationArrival/0/30/서울"]


# --- Java는 아래 경우 모두 가짜 도착 정보 [2, 5, 8, 11, 14]를 돌려줬다 ---


@pytest.mark.parametrize(
    ("station", "line"),
    [(None, "1호선"), ("서울역", None), ("서울역", "100호선")],
)
def test_invalid_input_raises_instead_of_mock_arrivals(station, line):
    client = client_with(responding(body(row("1001", "상행", "120"))))
    with pytest.raises(InvalidInputError):
        client.get_remaining_minutes(station, line, "상행")


@pytest.mark.parametrize(
    "text",
    [
        body(),                                             # row 없음
        body(row("1002", "상행", "120")),                    # 해당 호선 열차 없음
        "<RESULT><code>INFO-200</code><message>해당하는 데이터가 없습니다.</message></RESULT>",
        body(result="<RESULT><code>INFO-100</code><message>인증키가 유효하지 않습니다.</message></RESULT>"),
        "not xml",
    ],
    ids=["no-rows", "no-matching-line", "info-200", "info-100", "malformed"],
)
def test_empty_or_error_response_raises_unavailable(text):
    with pytest.raises(ArrivalUnavailableError):
        client_with(responding(text)).get_remaining_minutes("서울역", "1호선", "상행")


def test_json_error_body_reports_api_error_code():
    # 실제 API는 키가 틀리면 XML 경로로 요청해도 HTTP 200 + JSON 오류 본문을 준다.
    text = '{"status":500,"code":"INFO-100","message":"인증키가 유효하지 않습니다.","total":0}'
    with pytest.raises(ArrivalUnavailableError, match="INFO-100"):
        client_with(responding(text)).get_remaining_minutes("서울역", "1호선", "상행")


def test_unusable_response_is_logged(caplog):
    with pytest.raises(ArrivalUnavailableError):
        client_with(responding(body())).get_remaining_minutes("서울역", "1호선", "상행")
    assert any("response unusable" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("status", [400, 403, 500, 503])
def test_non_200_raises_unavailable(status):
    with pytest.raises(ArrivalUnavailableError, match=str(status)):
        client_with(responding("", status)).get_remaining_minutes("서울역", "1호선", "상행")


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")],
    ids=["connect", "timeout"],
)
def test_transport_failure_raises_unavailable(error):
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    with pytest.raises(ArrivalUnavailableError):
        client_with(handler).get_remaining_minutes("서울역", "1호선", "상행")



# --- 판단 불가 원인 분류 ---

T, P, N = UnavailableCause.TRANSIENT, UnavailableCause.PERMANENT, UnavailableCause.NO_TRAINS


@pytest.mark.parametrize(
    ("code", "cause"),
    [
        ("ERROR-500", T), ("ERROR-600", T),
        ("INFO-100", P), ("INFO-300", P), ("INFO-400", P), ("ERROR-300", P), ("ERROR-301", P),
        ("ERROR-310", P), ("ERROR-334", P), ("ERROR-336", P), ("ERROR-337", P), ("ERROR-404", P),
        ("ERROR-601", P),
        ("INFO-200", N),
        ("ERROR-599", T), ("ERROR-650", T),  # 표에 없는 서버 측 코드
        ("INFO-999", P), ("ERROR-999", P),  # 표에 없는 그 밖의 코드
    ],
)
def test_classify_api_code(code, cause):
    assert classify_api_code(code) is cause


def unavailable(text: str) -> ArrivalUnavailableError:
    with pytest.raises(ArrivalUnavailableError) as excinfo:
        client_with(responding(text)).get_remaining_minutes("서울역", "1호선", "상행")
    return excinfo.value


@pytest.mark.parametrize(
    ("text", "cause"),
    [
        ('{"status":500,"code":"INFO-100","message":"인증키가 유효하지 않습니다.","total":0}', P),
        ("<RESULT><code>ERROR-337</code><message>일별 트래픽 제한</message></RESULT>", P),
        ("<RESULT><code>INFO-200</code><message>해당하는 데이터가 없습니다.</message></RESULT>", N),
        (body(), N),                          # row 없음
        (body(row("1002", "상행", "120")), N),  # 해당 호선 열차 없음
        ("<html>점검 중</html>", T),
    ],
    ids=["info-100-json", "error-337", "info-200", "no-rows", "no-matching-line", "maintenance-page"],
)
def test_unavailable_cause_from_response(text, cause):
    error = unavailable(text)
    assert error.cause is cause
    assert error.retryable is (cause is T)


def test_http_4xx_is_permanent():
    assert unavailable_status(404).cause is UnavailableCause.PERMANENT


def unavailable_status(status: int) -> ArrivalUnavailableError:
    with pytest.raises(ArrivalUnavailableError) as excinfo:
        client_with(responding("", status)).get_remaining_minutes("서울역", "1호선", "상행")
    return excinfo.value
