# 지하철 출발 시점 판단 서비스 (Python)

[KakaoMCP](https://github.com/jinwoo1289/KakaoMCP)(Java/Spring)를 Python으로 다시 구현한 프로젝트입니다.
역까지의 이동 시간과 실시간 도착 정보로 **지금 나갈지(GO_NOW), 언제 나갈지(WAIT), 이미 늦었는지(TOO_LATE)** 를 판단합니다.

## 테스트

```bash
pip install -e ".[dev]"
pytest
```

## Java 버전과의 차이

### 판단 로직은 그대로 옮겼습니다

`DepartureTimingService.assess` / `savePreset`의 판단 로직을 그대로 옮겼고,
Java의 `DepartureTimingServiceTest` 7개를 pytest로 옮겨 같은 결과가 나오는지 확인했습니다.

- 버퍼 1분. 도착 예정 목록에서 `(이동 시간 + 버퍼)` 이후 처음 탈 수 있는 열차를 고릅니다.
- 역 대기 0분이면 `GO_NOW`, 양수면 `WAIT` + 추천 출발 시각(`HH:mm`), 탈 수 있는 열차가 없으면 `TOO_LATE`.
- `presetName`이 있으면 저장된 이동 시간이 `estimatedTimeToStation`보다 우선합니다.
