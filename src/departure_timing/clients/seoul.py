"""서울시 실시간 지하철 도착 정보 API 클라이언트 (Java SeoulArrivalClient 이식).

Java 버전은 실패하면 가짜 도착 정보 [2, 5, 8, 11, 14]를 돌려줬지만,
여기서는 도착 정보를 얻지 못하면 원인(일시적/영구/열차 없음)을 담은 ArrivalUnavailableError를 던진다.
"""

import json
import logging
import math
import random
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from departure_timing.domain import Arrivals
from departure_timing.errors import ArrivalUnavailableError, InvalidInputError, UnavailableCause
from departure_timing.lines import normalize_station, resolve_direction, subway_id_for

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://swopenAPI.seoul.go.kr/api/subway"
MAX_ARRIVALS = 5
_OK_CODE = "INFO-000"
_DIGITS = re.compile(r"[0-9]+")

# 서울 열린데이터광장 Open API 결과 코드 분류. (관찰)은 이 API에서 직접 확인한 코드.
# 이 API는 오류도 HTTP 200 + 본문 코드로 알린다.
_TRANSIENT_CODES = frozenset({"ERROR-500", "ERROR-600"})  # 서버 오류, DB 연결 오류
_NO_TRAINS_CODES = frozenset({"INFO-200"})  # 해당하는 데이터 없음 (역 이름 오류와 구분 불가)
_PERMANENT_CODES = frozenset({
    "INFO-100",  # 인증키 오류 (관찰)
    "INFO-300",  # 호출 건수 초과
    "INFO-400",  # 권한 없음
    "ERROR-300", "ERROR-301", "ERROR-310",  # 필수값 누락, 파일 타입, 서비스 없음
    "ERROR-331", "ERROR-332", "ERROR-333", "ERROR-334", "ERROR-335", "ERROR-336",  # 요청 범위 (334·336 관찰)
    "ERROR-337",  # 일별 트래픽 제한
    "ERROR-404",  # 서비스 없음 (관찰)
    "ERROR-601",  # SQL 문장 오류
})
_UNKNOWN_SERVER_ERROR = re.compile(r"ERROR-[56][0-9]{2}")


def classify_api_code(code: str) -> UnavailableCause:
    if code in _NO_TRAINS_CODES:
        return UnavailableCause.NO_TRAINS
    if code in _TRANSIENT_CODES:
        return UnavailableCause.TRANSIENT
    if code in _PERMANENT_CODES:
        return UnavailableCause.PERMANENT
    # 표에 없는 코드: 서버 측(5xx/6xx)은 일시적, 그 밖은 영구로 본다.
    if _UNKNOWN_SERVER_ERROR.fullmatch(code):
        return UnavailableCause.TRANSIENT
    return UnavailableCause.PERMANENT


# arvlCd(도착 코드): 0 진입, 1 도착, 2 출발, 3 전역 출발, 4 전역 진입, 5 전역 도착, 99 운행 중
_AT_STATION_CODES = frozenset({"0", "1"})  # 지금 이 역에 들어오거나 서 있는 열차
_DEPARTING_CODE = "2"  # 이 역을 떠나는 열차: 탈 수 없고, 도착 예정 열차도 아니다


@dataclass(frozen=True)
class _Row:
    subway_id: str | None
    updn_line: str | None
    barvl_dt: str | None
    arvl_cd: str | None = None


def _text(element: ET.Element, tag: str) -> str | None:
    child = element.find(tag)
    return child.text if child is not None else None


class _RetryableFailure(Exception):
    """다시 시도할 만한 일시적 실패 (타임아웃, 연결 실패, 5xx, 429, 본문 서버 오류 코드)."""


def _raise_api_error(code: str, message: str) -> None:
    detail = f"API {code}: {message}" if message else f"API {code}"
    cause = classify_api_code(code)
    if cause is UnavailableCause.TRANSIENT:
        raise _RetryableFailure(detail)
    raise ArrivalUnavailableError(detail, cause)


def _raise_for_json_error(body: str) -> None:
    """인증키 오류 등은 XML을 요청해도 JSON 본문({"code": "INFO-100", ...})으로 온다."""
    try:
        payload = json.loads(body)
    except ValueError:
        return
    if isinstance(payload, dict):
        error = payload.get("errorMessage", payload)
        if isinstance(error, dict) and error.get("code"):
            _raise_api_error(str(error["code"]), str(error.get("message", "")))


def _parse_rows(body: str) -> list[_Row]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        _raise_for_json_error(body)
        # 점검 페이지(HTML) 등. 시간이 지나면 풀릴 수 있지만, 같은 요청을 곧바로 다시 보내지는 않는다.
        raise ArrivalUnavailableError(
            "unparseable API response", UnavailableCause.TRANSIENT
        ) from e

    results = [root] if root.tag == "RESULT" else list(root.iter("RESULT"))
    for result in results:
        code = (_text(result, "code") or "").strip()
        if code and code != _OK_CODE:
            _raise_api_error(code, (_text(result, "message") or "").strip())

    # XML이지만 이 API의 형식이 아니면(점검 페이지 등) "열차 없음"으로 오인하지 않는다.
    if root.tag != "realtimeStationArrival":
        raise ArrivalUnavailableError(f"unexpected response root <{root.tag}>", UnavailableCause.TRANSIENT)

    return [
        _Row(
            _text(row, "subwayId"),
            _text(row, "updnLine"),
            _text(row, "barvlDt"),
            (_text(row, "arvlCd") or "").strip() or None,
        )
        for row in root.iter("row")
    ]


def _seconds_to_minutes_ceil(seconds: str | None) -> int | None:
    if seconds is None:
        return None
    seconds = seconds.strip()
    if not _DIGITS.fullmatch(seconds):
        return None
    sec = int(seconds)
    return 0 if sec == 0 else math.ceil(sec / 60)


def _arrival_minutes(row: _Row) -> int | None:
    """도착까지 남은 분. 도착 시간을 모르면 None.

    API는 도착 시간을 모를 때도 barvlDt=0을 준다(예: "[5]번째 전역"). 그래서 0은
    arvlCd가 진입·도착일 때만 "지금 들어오는 열차"로 보고, 그 밖의 0은 모르는 것으로 본다.
    몇 번째 전 역인지로 분을 추정하지는 않는다.
    """
    if row.arvl_cd in _AT_STATION_CODES:
        return 0
    minutes = _seconds_to_minutes_ceil(row.barvl_dt)
    return minutes if minutes else None


@dataclass(frozen=True)
class RetryPolicy:
    """도착 정보 조회 재시도 기준.

    조회(GET)라 몇 번을 다시 보내도 결과가 같으므로(멱등) 재시도해도 안전하다.
    다만 실시간 정보라 오래 기다린 답은 쓸모가 없으므로 전체 시간 예산 안에서만 재시도한다.
    """

    max_attempts: int = 3
    base_delay: float = 0.3      # 0.3초, 0.6초, ... (지수 백오프)
    max_jitter: float = 0.1      # 백오프에 더하는 0 ~ max_jitter초 무작위 지연
    request_timeout: float = 1.5  # 시도 1회의 httpx 타임아웃 (남은 예산보다 길어지지 않음)
    total_budget: float = 3.0    # 모든 시도와 대기를 합친 시간 상한

    def backoff(self, attempt: int, jitter: float) -> float:
        return self.base_delay * 2 ** (attempt - 1) + jitter * self.max_jitter


class SeoulArrivalClient:
    def __init__(
        self,
        api_key: str,
        http: httpx.Client,
        base_url: str = DEFAULT_BASE_URL,
        retry: RetryPolicy = RetryPolicy(),
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._api_key = api_key
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._retry = retry
        self._sleep = sleep
        self._clock = clock
        self._jitter = jitter

    def get_arrivals(
        self, station: str | None, line: str | None, direction: str | None
    ) -> Arrivals:
        target_subway_id = subway_id_for(line)
        if not station or not station.strip():
            raise InvalidInputError("station(역 이름)을 입력해 주세요.")
        if target_subway_id is None:
            raise InvalidInputError(f"지원하지 않는 호선입니다: {line}")
        normalized_dir = resolve_direction(direction, line)

        rows = self._fetch_rows(self._url(station))
        if not rows:
            logger.warning("seoul arrival API response unusable: no rows station=%s", station)
            raise ArrivalUnavailableError("no rows", UnavailableCause.NO_TRAINS)

        known: list[int] = []
        unknown = 0
        for row in rows:
            if row.subway_id != target_subway_id or row.updn_line != normalized_dir:
                continue
            if row.arvl_cd == _DEPARTING_CODE:
                continue
            minutes = _arrival_minutes(row)
            if minutes is None:
                unknown += 1
            else:
                known.append(minutes)
        live = sorted(known)[:MAX_ARRIVALS]

        logger.info(
            "arrivals station=%s line=%s direction=%s rows=%d live=%s unknown=%d",
            station, line, direction, len(rows), live, unknown,
        )
        if not live:
            cause = UnavailableCause.UNKNOWN_ARRIVAL_TIMES if unknown else UnavailableCause.NO_TRAINS
            logger.warning(
                "seoul arrival API response unusable: %s station=%s line=%s direction=%s unknown=%d",
                cause.value, station, line, direction, unknown,
            )
            raise ArrivalUnavailableError(f"no known arrival times (unknown={unknown})", cause)
        return Arrivals(live, unknown)

    def _url(self, station: str) -> str:
        # API 키가 경로에 들어가므로 이 URL은 로그에 남기지 않는다.
        return (
            f"{self._base_url}/{quote(self._api_key, safe='')}/xml/realtimeStationArrival/0/30/"
            f"{quote(normalize_station(station), safe='')}"
        )

    def _fetch_rows(self, url: str) -> list[_Row]:
        """일시적 실패만 제한된 횟수·시간 안에서 재시도한다.

        재시도: 타임아웃, 연결 실패, HTTP 5xx·429, 본문의 서버 오류 코드(ERROR-500·600 등).
        4xx(429 제외), 그 밖의 본문 오류 코드, 빈 결과는 다시 보내도 같으므로 재시도하지 않는다.
        """
        policy = self._retry
        deadline = self._clock() + policy.total_budget
        attempt = 0
        reason = ""

        while attempt < policy.max_attempts:
            attempt += 1
            timeout = min(policy.request_timeout, max(deadline - self._clock(), 0.0))
            try:
                rows = _parse_rows(self._fetch_once(url, timeout))
            except _RetryableFailure as e:
                reason = str(e)
                logger.warning(
                    "seoul arrival API attempt %d/%d failed: %s",
                    attempt, policy.max_attempts, reason,
                )
            except ArrivalUnavailableError as e:
                logger.warning(
                    "seoul arrival API attempt %d/%d failed (not retryable): %s",
                    attempt, policy.max_attempts, e,
                )
                raise
            else:
                if attempt > 1:
                    logger.info("seoul arrival API succeeded on attempt %d", attempt)
                return rows

            if attempt == policy.max_attempts:
                break
            delay = policy.backoff(attempt, self._jitter())
            if self._clock() + delay >= deadline:
                logger.warning(
                    "seoul arrival API retry stopped: time budget %.1fs exhausted",
                    policy.total_budget,
                )
                break
            self._sleep(delay)

        raise ArrivalUnavailableError(
            f"API call failed ({attempt}회 시도, 마지막 사유: {reason})", UnavailableCause.TRANSIENT
        )

    def _fetch_once(self, url: str, timeout: float) -> str:
        try:
            response = self._http.get(url, timeout=timeout)
        except httpx.TimeoutException as e:
            raise _RetryableFailure(f"timeout ({type(e).__name__})") from e
        except httpx.TransportError as e:
            raise _RetryableFailure(f"connection error ({type(e).__name__})") from e

        status = response.status_code
        if status == 200:
            return response.text
        if status == 429 or status >= 500:
            raise _RetryableFailure(f"HTTP {status}")
        raise ArrivalUnavailableError(f"HTTP {status}", UnavailableCause.PERMANENT)
