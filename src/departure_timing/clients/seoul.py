"""서울시 실시간 지하철 도착 정보 API 클라이언트 (Java SeoulArrivalClient 이식).

Java 버전은 실패하면 가짜 도착 정보 [2, 5, 8, 11, 14]를 돌려줬지만,
여기서는 도착 정보를 얻지 못하면 ArrivalUnavailableError를 던진다.
"""

import logging
import math
import re
import xml.etree.ElementTree as ET
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


class SeoulArrivalClient:
    def __init__(
        self,
        api_key: str,
        http: httpx.Client,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 2.0,
    ) -> None:
        self._api_key = api_key
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

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
        try:
            response = self._http.get(url, timeout=self._timeout)
        except httpx.TimeoutException as e:
            logger.warning("seoul arrival API failed: timeout (%s)", type(e).__name__)
            raise ArrivalUnavailableError("도착 정보 API 응답 시간이 초과되었습니다.") from e
        except httpx.TransportError as e:
            logger.warning("seoul arrival API failed: transport error (%s)", type(e).__name__)
            raise ArrivalUnavailableError("도착 정보 API에 연결하지 못했습니다.") from e

        if response.status_code != 200:
            logger.warning("seoul arrival API failed: HTTP %d", response.status_code)
            raise ArrivalUnavailableError(
                f"도착 정보 API가 HTTP {response.status_code}로 응답했습니다."
            )
        return response.text
