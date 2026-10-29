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

### 외부 API 실패 시 가짜 데이터 대신 "판단 불가"로 응답합니다

Java의 `SeoulArrivalClient`는 역/호선이 비었거나, API 응답이 비었거나, 모르는 호선이거나, 호출이 실패하면
가짜 도착 정보 `[2, 5, 8, 11, 14]`를 돌려줬습니다. 그래서 사용자는 실제 열차와 무관한 판단을 정상 결과로 받았습니다.
또 서비스는 도착 정보가 비어 있거나 이동 시간이 없을 때 `WAIT`를 돌려줬습니다.

이 버전은 실패를 판단 결과와 분리합니다.

| 상황 | Java | Python |
|---|---|---|
| 역/호선 누락, 모르는 호선 | 가짜 도착 정보로 판단 | `400` 입력 오류 |
| 이동 시간 누락·0 이하 | `WAIT` | `400` 입력 오류 |
| 없는 프리셋 | `estimatedTimeToStation`으로 대체 | `400` 입력 오류 |
| 프리셋 저장 입력 오류 | `200` + `success: false` | `400` 입력 오류 |
| API 호출 실패·HTTP 오류·해석 불가 | 가짜 도착 정보로 판단 | `502` 판단 불가 |
| API 응답이 비었거나 해당 호선/방향 열차 없음 | 가짜 도착 정보로 판단 | `502` 판단 불가 |
| API 본문 오류 코드(`INFO-200`, `INFO-100` 등) | 가짜 도착 정보로 판단 | `502` 판단 불가 |

- `GO_NOW` / `WAIT` / `TOO_LATE`는 실제 도착 정보가 있을 때만 반환합니다.
- 입력 검증을 외부 API 호출보다 먼저 합니다. API가 장애 중이어도 입력 오류는 입력 오류로 알립니다.
