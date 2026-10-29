"""서울시 실시간 지하철 도착 정보 API 클라이언트 (Java SeoulArrivalClient 이식).

Java 버전은 실패하면 가짜 도착 정보 [2, 5, 8, 11, 14]를 돌려줬지만,
여기서는 도착 정보를 얻지 못하면 ArrivalUnavailableError를 던진다.
"""

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

from departure_timing.errors import ArrivalUnavailableError, InvalidInputError
from departure_timing.lines import normalize_direction, normalize_station, subway_id_for

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://swopenAPI.seoul.go.kr/api/subway"
MAX_ARRIVALS = 5
_OK_CODE = "INFO-000"
_DIGITS = re.compile(r"[0-9]+")


@dataclass(frozen=True)
class _Row:
    subway_id: str | None
    updn_line: str | None
    barvl_dt: str | None


def _text(element: ET.Element, tag: str) -> str | None:
    child = element.find(tag)
    return child.text if child is not None else None


def _parse_rows(body: str) -> list[_Row]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise ArrivalUnavailableError("도착 정보 API 응답을 해석할 수 없습니다.") from e

    # 이 API는 오류도 HTTP 200 + 본문의 RESULT 코드로 알린다 (예: INFO-200 데이터 없음, INFO-100 인증키 오류).
    results = [root] if root.tag == "RESULT" else list(root.iter("RESULT"))
    for result in results:
        code = (_text(result, "code") or "").strip()
        if code and code != _OK_CODE:
            message = (_text(result, "message") or "").strip()
            raise ArrivalUnavailableError(f"도착 정보 API 오류 {code}: {message}")

    return [
        _Row(_text(row, "subwayId"), _text(row, "updnLine"), _text(row, "barvlDt"))
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


class _RetryableFailure(Exception):
    """다시 시도할 만한 일시적 실패 (타임아웃, 연결 실패, 5xx, 429)."""


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

    def get_remaining_minutes(
        self, station: str | None, line: str | None, direction: str | None
    ) -> list[int]:
        target_subway_id = subway_id_for(line)
        if not station or not station.strip():
            raise InvalidInputError("station(역 이름)을 입력해 주세요.")
        if target_subway_id is None:
            raise InvalidInputError(f"지원하지 않는 호선입니다: {line}")

        rows = _parse_rows(self._fetch(self._url(station)))
        if not rows:
            raise ArrivalUnavailableError(f"'{station}'의 실시간 도착 정보가 비어 있습니다.")

        normalized_dir = normalize_direction(direction, line)
        minutes = (
            _seconds_to_minutes_ceil(row.barvl_dt)
            for row in rows
            if row.subway_id == target_subway_id
            and (normalized_dir is None or normalized_dir == row.updn_line)
        )
        live = sorted(m for m in minutes if m is not None and m > 0)[:MAX_ARRIVALS]

        logger.info(
            "arrivals station=%s line=%s direction=%s rows=%d live=%s",
            station, line, direction, len(rows), live,
        )
        if not live:
            raise ArrivalUnavailableError(
                f"'{station}' {line} 방면에 도착 예정인 열차 정보가 없습니다."
            )
        return live

    def _url(self, station: str) -> str:
        # API 키가 경로에 들어가므로 이 URL은 로그에 남기지 않는다.
        return (
            f"{self._base_url}/{quote(self._api_key, safe='')}/xml/realtimeStationArrival/0/30/"
            f"{quote(normalize_station(station), safe='')}"
        )

    def _fetch(self, url: str) -> str:
        """일시적 실패만 제한된 횟수·시간 안에서 재시도한다.

        4xx(429 제외)와 정상 응답 본문의 오류·빈 결과는 다시 보내도 같으므로 재시도하지 않는다.
        """
        policy = self._retry
        deadline = self._clock() + policy.total_budget
        attempt = 0
        reason = ""

        while attempt < policy.max_attempts:
            attempt += 1
            timeout = min(policy.request_timeout, max(deadline - self._clock(), 0.0))
            try:
                text = self._fetch_once(url, timeout)
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
                return text

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
            f"도착 정보 API 호출에 실패했습니다 ({attempt}회 시도, 마지막 사유: {reason})."
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
        raise ArrivalUnavailableError(f"도착 정보 API가 HTTP {status}로 응답했습니다.")
