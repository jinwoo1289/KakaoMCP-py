import os
from dataclasses import dataclass

from departure_timing.clients.seoul import DEFAULT_BASE_URL


@dataclass(frozen=True)
class Settings:
    seoul_api_key: str
    seoul_base_url: str = DEFAULT_BASE_URL

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.environ.get("SEOUL_SUBWAY_APIKEY", "").strip()
        if not api_key:
            raise RuntimeError("SEOUL_SUBWAY_APIKEY 환경변수에 서울시 실시간 도착 API 키를 설정해 주세요.")
        return cls(
            seoul_api_key=api_key,
            seoul_base_url=os.environ.get("SEOUL_SUBWAY_BASE_URL", DEFAULT_BASE_URL),
        )
