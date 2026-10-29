# 지하철 출발 시점 판단 서비스 (Python)

[KakaoMCP](https://github.com/jinwoo1289/KakaoMCP)(Java/Spring)를 Python으로 다시 구현한 프로젝트입니다.
역까지의 이동 시간과 실시간 도착 정보로 **지금 나갈지(GO_NOW), 언제 나갈지(WAIT), 이미 늦었는지(TOO_LATE)** 를 판단합니다.

같은 판단 기능을 REST API와 MCP 서버(공식 MCP Python SDK, Streamable HTTP) 두 가지로 제공합니다.

## 실행

Python 3.12 이상과 [서울 열린데이터광장](https://data.seoul.go.kr/) 실시간 지하철 도착정보 API 키가 필요합니다.

```bash
pip install -e ".[dev]"
export SEOUL_SUBWAY_APIKEY=발급받은_키
uvicorn departure_timing.api:create_app --factory --port 8000
```

Docker:

```bash
docker build -t departure-timing .
docker run --rm -p 8000:8000 -e SEOUL_SUBWAY_APIKEY=발급받은_키 departure-timing
```

| 환경변수 | 필수 | 설명 |
|---|---|---|
| `SEOUL_SUBWAY_APIKEY` | 예 | 서울시 실시간 도착 API 키. 없으면 서버가 기동하지 않습니다. |
| `SEOUL_SUBWAY_BASE_URL` | 아니오 | 기본값 `http://swopenAPI.seoul.go.kr/api/subway` |
| `MCP_ALLOWED_HOSTS` | 배포 시 | `/mcp`에 접근을 허용할 Host 목록(쉼표 구분, 예: `mcp.example.com,mcp.example.com:*`). 없으면 localhost만 허용합니다. |

- 프리셋은 프로세스 메모리에 저장됩니다(Java 버전과 같음). 재시작하면 사라지며, 워커를 여러 개 띄우면 워커끼리 공유되지 않으므로 워커는 1개로 실행하세요.
- `currentTime`이 없으면 서버 시간대와 상관없이 한국 시각(Asia/Seoul)을 기준으로 계산합니다.
- API 문서: 서버 실행 후 `http://localhost:8000/docs`

## 테스트

```bash
pytest
```

외부 API를 실제로 호출하는 테스트는 없습니다. 서울시 API 응답은 `httpx.MockTransport`로 대체합니다.
MCP 도구는 SDK의 메모리 내 클라이언트(`mcp.client.client.Client`)로 검증하고, `/mcp` HTTP 마운트는 `TestClient`로 확인합니다.

## API 예시

### `GET /health`

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

### `POST /presets` — 이동 시간 저장

```bash
curl -X POST http://localhost:8000/presets \
  -H 'Content-Type: application/json' \
  -d '{"presetName": "home", "estimatedTimeToStation": 10}'
# 200 {"success":true,"message":"프리셋 'home' 저장 완료"}
```

### `POST /departure/assess` — 출발 시점 판단

| 필드 | 타입 | 설명 |
|---|---|---|
| `station` | string | 역 이름 (필수, 예: `신대방역`) |
| `line` | string | 호선 (필수, 예: `2호선`, `2`, `신분당선`) |
| `direction` | string | 방향 (필수, `상행`/`하행`, 2호선은 `내선`/`외선`) |
| `estimatedTimeToStation` | int | 역까지 이동 시간(분) |
| `presetName` | string | 저장된 프리셋. 있으면 `estimatedTimeToStation`보다 우선 |
| `currentTime` | `HH:mm` | 현재 시각 (생략하면 서버의 한국 시각) |

```bash
curl -X POST http://localhost:8000/departure/assess \
  -H 'Content-Type: application/json' \
  -d '{"station": "신대방역", "line": "2호선", "direction": "내선", "presetName": "home"}'
```

```json
{"decision": "WAIT", "recommendedDepartureTime": "08:35", "reason": "지금 출발하면 역에서 약 5분 대기합니다. 08:35에 출발을 추천합니다.", "recheckAfterMinutes": null}
```

| `decision` | 의미 | `recommendedDepartureTime` | `recheckAfterMinutes` |
|---|---|---|---|
| `GO_NOW` | 지금 출발하면 대기 없이 탑승 | `"NOW"` | `null` |
| `WAIT` | 추천 시각에 출발 | `"HH:mm"` | `null` |
| `TOO_LATE` | 도착 예정 열차로는 탑승이 어려움 | `null` | 첫 열차 도착까지 남은 분 |

`TOO_LATE`일 때는 도착 목록의 첫 열차가 지나가야 목록이 갱신되므로, 그 시간을 재확인 시점으로 알려 줍니다.

```json
{"decision": "TOO_LATE", "recommendedDepartureTime": null, "reason": "현재 도착 예정인 열차들로는 탑승이 어렵습니다. 첫 열차가 약 2분 뒤 도착하니, 그 이후에 다시 확인해 주세요.", "recheckAfterMinutes": 2}
```

### 오류 응답

판단할 수 없는 경우에는 판단 결과 대신 오류를 돌려줍니다.

```json
// 400 입력 오류: 역/호선 누락, 모르는 호선, 이동 시간 누락·0 이하, 없는 프리셋, 잘못된 형식
{"error": "INVALID_INPUT", "message": "지원하지 않는 호선입니다: 100호선"}

// 502 판단 불가: 원인에 따라 message와 retryable이 다름
{"error": "ARRIVAL_UNAVAILABLE", "message": "실시간 정보 서버가 응답하지 않습니다. 잠시 후 다시 시도해 주세요.", "retryable": true}
```

| 원인 | 언제 | `message` | `retryable` |
|---|---|---|---|
| 일시적 실패 | 타임아웃·연결 실패·HTTP 5xx·429 또는 본문 `ERROR-500`·`600`이 재시도 3회 뒤에도 계속, 해석할 수 없는 응답 | 실시간 정보 서버가 응답하지 않습니다. 잠시 후 다시 시도해 주세요. | `true` |
| 영구적 실패 | API 키 오류, 호출 한도 초과, 요청 형식 오류 등 서울시 API 오류 코드, HTTP 4xx | 실시간 정보 서비스 설정 문제로 조회할 수 없습니다. 다시 시도해도 해결되지 않습니다. | `false` |
| 열차 정보 없음 | 정상 응답인데 해당 호선·방향 열차가 없음, `INFO-200` | 현재 운행 중인 열차 정보가 없습니다. | `false` |

응답에는 고정 문구만 넣습니다. 서울시 API 오류 코드와 원문 메시지(키 관련 내용 포함)는 서버 로그에만 남깁니다.

#### 서울시 API 결과 코드 분류

이 API는 오류도 HTTP 200과 본문 코드로 알립니다. ✔는 이 API에서 직접 관찰한 코드이고,
나머지는 서울 열린데이터광장 Open API 공통 코드표 기준입니다(데이터셋 문서에는 코드 목록이 없음).

| 코드 | 의미 | 분류 |
|---|---|---|
| `ERROR-500`, `ERROR-600` | 서버 오류, DB 연결 오류 | 일시적 (재시도함) |
| `INFO-100` ✔ | 인증키 오류 | 영구 |
| `INFO-300`, `ERROR-337` | 호출 건수·일별 트래픽 한도 초과 | 영구 |
| `INFO-400` | 권한 없음 | 영구 |
| `ERROR-300`·`301`·`310`·`331`~`336` (`334`·`336` ✔), `ERROR-404` ✔ | 필수값·형식·요청 범위 오류, 서비스 없음 | 영구 |
| `ERROR-601` | SQL 문장 오류 | 영구 |
| `INFO-200` | 해당하는 데이터 없음 (역 이름 오류와 구분 불가) | 열차 정보 없음 |
| 표에 없는 코드 | — | `ERROR-5xx`·`6xx`는 일시적, 그 밖은 영구 |

## MCP 연결

| 항목 | 값 |
|---|---|
| 엔드포인트 | `http://<호스트>:8000/mcp` (`/mcp/`도 같음) |
| 전송 방식 | Streamable HTTP, stateless, JSON 응답 |
| 서버 이름 | `departure-timing-mcp` |
| 프로토콜 버전 | 2024-11-05 ~ 2025-11-25, 2026-07-28 (SDK가 협상) |

- 로컬이 아닌 곳에 배포하면 `MCP_ALLOWED_HOSTS`에 외부 Host를 지정하세요.
  지정하지 않으면 SDK의 DNS rebinding 보호가 localhost 외 Host를 `421`로 거절합니다(REST는 영향 없음).
- REST와 MCP는 같은 프리셋 저장소를 씁니다. `POST /presets`로 저장한 프리셋을 MCP 도구에서도 쓸 수 있습니다.

### 도구

이름과 입력 스키마(속성 이름, 타입, 설명, 필수 항목)는 Java 버전과 같습니다.
단, `assess_departure_timing`의 `direction`은 설명을 보완하고 필수로 바꿨습니다(아래 "방향 값을 검증해…" 참고).

| 도구 | 입력 | 필수 | 결과 |
|---|---|---|---|
| `assess_departure_timing` | `station`, `line`, `estimatedTimeToStation`(number), `direction`, `presetName` | `station`, `line`, `direction` | `{decision, recommendedDepartureTime, reason}` |
| `save_preset` | `presetName`, `estimatedTimeToStation`(number) | 둘 다 | `{success, message}` |

결과는 REST 응답과 같은 JSON으로, `structuredContent`와 텍스트(JSON 문자열) 두 형태로 함께 돌려줍니다.
`estimatedTimeToStation`은 정수 값(`10`, `10.0`)만 받고, `7.5`처럼 소수가 있으면 입력 오류입니다.

### 호출 예시

공식 Python SDK 클라이언트:

```python
import asyncio
from mcp.client.client import Client

async def main():
    async with Client("http://localhost:8000/mcp") as client:
        await client.call_tool("save_preset", {"presetName": "home", "estimatedTimeToStation": 10})
        result = await client.call_tool(
            "assess_departure_timing",
            {"station": "신대방역", "line": "2호선", "direction": "내선", "presetName": "home"},
        )
        print(result.is_error, result.structured_content)

asyncio.run(main())
```

JSON-RPC로 직접 호출:

```bash
curl -X POST http://localhost:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2025-06-18' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"assess_departure_timing","arguments":{"station":"신대방역","line":"2호선","direction":"내선","estimatedTimeToStation":10}}}'
```

```json
{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"{\"decision\": \"WAIT\", ...}"}],"structuredContent":{"decision":"WAIT","recommendedDepartureTime":"08:35","reason":"..."},"isError":false}}
```

### 오류

[MCP 규격](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#error-handling)의
"Error Handling"(2025-11-25, 2026-07-28 동일) 기준으로 나눕니다.

| 상황 | 종류 | 응답 |
|---|---|---|
| 없는 도구 | 프로토콜 오류 | JSON-RPC `error.code = -32602` |
| 스키마 위반(필수값 누락, 타입 오류) | 도구 실행 오류 | `isError: true`, `"[입력 오류] line: Field required"` |
| 모르는 호선, 없는 프리셋, 이동 시간 0 이하·소수, 빈 역 이름 | 도구 실행 오류 | `isError: true`, `"[입력 오류] 지원하지 않는 호선입니다: 100호선"` |
| 외부 API 일시적 실패(재시도 후) | 도구 실행 오류 | `isError: true`, `"[판단 불가] 실시간 정보 서버가 응답하지 않습니다. 잠시 후 다시 시도해 주세요."` |
| 서울시 API 설정 문제(키 오류 등) | 도구 실행 오류 | `isError: true`, `"[판단 불가] 실시간 정보 서비스 설정 문제로 조회할 수 없습니다. 다시 시도해도 해결되지 않습니다."` |
| 해당 호선·방향 열차 없음 | 도구 실행 오류 | `isError: true`, `"[판단 불가] 현재 운행 중인 열차 정보가 없습니다."` |

`[입력 오류]`는 인자를 고치면 해결되고, `[판단 불가]`는 같은 인자로 나중에 다시 시도하면 됩니다.
규격상 도구 실행 오류는 LLM에 그대로 전달되어 스스로 고쳐 다시 시도하는 데 쓰입니다.

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

### 외부 도착 정보 API를 제한적으로 재시도합니다

Java 버전은 재시도 없이 바로 가짜 데이터로 대체했습니다. 이 버전은 일시적 실패만 짧게 다시 시도하고,
그래도 실패하면 위 기준대로 `502` 판단 불가로 응답합니다(가짜 데이터는 쓰지 않습니다).

| 항목 | 기준 |
|---|---|
| 재시도 대상 | 타임아웃, 연결 실패, HTTP 5xx, HTTP 429, 본문의 서버 오류 코드(`ERROR-500`·`600` 등) |
| 재시도 안 함 | 그 밖의 4xx, 그 밖의 본문 오류 코드(키 오류 등), 해석할 수 없는 응답, 정상 응답이지만 열차 정보 없음 |
| 횟수 | 최대 3회 시도 |
| 대기 | 지수 백오프 0.3초 → 0.6초 + 지터 0~0.1초 |
| 시간 예산 | 시도·대기를 합쳐 약 3초. 다음 대기가 예산을 넘으면 그 자리에서 포기 |
| 요청별 타임아웃 | 1.5초, 단 남은 예산보다 길게 잡지 않음 |
| 로그 | 시도마다 `attempt n/3`과 실패 사유 (API 키가 든 URL은 남기지 않음) |

- **재시도해도 안전한 이유:** 도착 정보 조회는 읽기 전용 GET 요청이라 여러 번 보내도 결과가 같습니다(멱등).
- **시간 예산을 둔 이유:** 실시간 도착 정보는 몇 초만 지나도 판단 근거가 낡습니다.
  오래 기다려 받은 답보다 빨리 "판단 불가"를 알리는 편이 낫습니다.
- **4xx와 빈 응답을 재시도하지 않는 이유:** 다시 보내도 같은 결과가 나오기 때문입니다
  (잘못된 키, 없는 역, 지금 운행하는 열차 없음 등).

### 그 밖의 차이

- **엔드포인트 경로:** `/mcp/assess` → `/departure/assess`, `/mcp/presets` → `/presets`, 상태 확인은 `GET /health`.
  JSON 필드 이름(camelCase)과 응답 형식은 Java와 같습니다.
- **API 키 필수:** Java는 키가 없어도 기동한 뒤 가짜 데이터로 응답할 수 있었지만, 이 버전은 키가 없으면 기동하지 않습니다.
- **API 키 오류 해석:** 서울시 API는 키가 틀리면 XML을 요청해도 JSON 오류 본문을 돌려줍니다.
  이 경우에도 오류 코드(`INFO-100` 등)를 읽어 영구적 실패로 분류합니다(코드는 로그에만 남김).
- **기준 시각:** `currentTime`을 생략하면 서버 시간대와 무관하게 한국 시각을 씁니다(Java는 서버 로컬 시각).

### "잠시 후 다시 시도" 대신 근거 있는 안내를 합니다

Java는 `TOO_LATE`와 도착 정보 실패에 근거 없이 "잠시 후 다시 시도"만 안내했습니다. 이 버전은 근거가 있는 정보만 알려 줍니다.

- **TOO_LATE:** 도착 목록에서 첫 열차가 도착하기까지 남은 분을 계산해
  "첫 열차가 약 N분 뒤 도착하니, 그 이후에 다시 확인해 주세요"라고 안내하고 `recheckAfterMinutes`에 N을 담습니다.
  고정된 대기 시간(예: 3~4분)은 쓰지 않습니다. 첫 열차가 지나가야 도착 목록이 갱신되기 때문입니다.
- **판단 불가:** 원인을 일시적 실패, 영구적 실패(설정 문제), 열차 정보 없음으로 나눕니다.
  "잠시 후 다시 시도"는 다시 시도해서 풀릴 수 있는 일시적 실패에만 안내하고, REST 응답에는 `retryable`을 담습니다.
  (분류 기준은 위 "오류 응답"의 표 참고)
- **본문 서버 오류 코드도 재시도합니다:** 서울시 API는 서버 오류를 HTTP 200 + `ERROR-500`·`600`으로 알리므로,
  HTTP 5xx와 같은 기준(최대 3회, 약 3초)으로 재시도합니다.

### 방향 값을 검증해 반대 방향 열차가 섞인 판단을 막습니다

**문제:** Java(`SeoulArrivalClient.normalizeDirection`)는 방향이 2호선에 상행/하행으로 오거나, 다른 호선에 내선/외선으로 오거나,
인식할 수 없는 값이면 방향 필터를 조용히 껐습니다. 그러면 반대 방향 열차까지 섞인 목록으로 판단해 정상 결과처럼 돌려줍니다.
실제 호출에서도 강남역 2호선을 `내선`으로 물으면 `TOO_LATE`였는데, `상행`으로 물으면 내선·외선 열차가 섞여
`WAIT 15:57`이 나왔습니다. 도구 설명도 "상행/하행"뿐이라 LLM이 2호선에 상행/하행을 넣기 쉬웠습니다.

**수정:** 방향을 필수로 바꾸고 호선에 맞는 값만 받습니다. 그 밖의 값은 외부 API를 부르기 전에 입력 오류(REST `400`, MCP `[입력 오류]`)로 알립니다.

| 입력 | Java | Python |
|---|---|---|
| 2호선 + `내선`/`외선` | 해당 방향만 | 해당 방향만 |
| 다른 호선 + `상행`/`하행` (`up`/`down`) | 해당 방향만 | 해당 방향만 |
| 2호선 + `상행`/`하행` | 필터 끔(양방향 섞임) | 입력 오류 "2호선은 방향을 내선 또는 외선으로 입력해 주세요." |
| 다른 호선 + `내선`/`외선` | 필터 끔(양방향 섞임) | 입력 오류 "{호선}은 방향을 상행 또는 하행으로 입력해 주세요." |
| 인식할 수 없는 값(`왼쪽`, `강남 방면` 등) | 필터 끔(양방향 섞임) | 입력 오류 "알 수 없는 방향입니다: …" |
| 생략(빈 문자열 포함) | 필터 없음(양방향 섞임) | 입력 오류 "방향(direction)이 필요합니다. …" |

- 상행↔내선 같은 변환은 하지 않습니다. 2호선의 상행이 내선인지 외선인지는 역마다 다르기 때문입니다.
- 도구 설명(MCP, REST)을 "상행/하행 (2호선은 내선/외선)"으로 보완했습니다.
- **방향을 필수로 바꾼 이유:** 생략해도 반대 방향 열차가 섞이는 같은 문제가 생기고, "어느 열차를 탈지"는 방향 없이는 판단할 수 없습니다.
  Java 스키마(`required: [station, line]`)와 달라지므로, `direction`을 보내지 않던 기존 클라이언트는 입력 오류를 받습니다.
  MCP에서는 오류 메시지에 호선별로 유효한 값이 들어 있어 LLM이 사용자에게 방향을 물어 다시 호출할 수 있습니다.
  MCP 스키마의 `required`에도 넣었으므로 누락 시 메시지는 SDK 검증 형식(`[입력 오류] direction: Field required`)입니다.

### MCP: 공식 SDK를 쓰고, 오류를 규격대로 나눕니다

Java 버전은 JSON-RPC를 직접 구현했습니다(`McpRequestDispatcher`). 이 버전은 공식 MCP Python SDK(`mcp` 2.x의 `MCPServer`.
v1의 `FastMCP`가 v2에서 이름이 바뀐 것)를 쓰고, 프로토콜 버전 협상과 Streamable HTTP 전송은 SDK가 처리합니다.
도구 이름과 입력 스키마는 Java와 같으므로 기존 클라이언트(PlayMCP 등)가 같은 인자로 호출할 수 있습니다.

| 상황 | Java | Python |
|---|---|---|
| 외부 API 실패 | `-32603` Internal error (실제로는 가짜 도착 정보로 판단해 정상 결과) | 도구 실행 오류 `isError` + `[판단 불가]` |
| 빈 `station` / `line` | `-32602` | 도구 실행 오류 `isError` + `[입력 오류]` |
| 모르는 호선, 없는 프리셋, 이동 시간 0 이하 | 정상 결과(가짜 데이터로 판단, 또는 `WAIT`) | 도구 실행 오류 `isError` + `[입력 오류]` |
| 소수 이동 시간(`7.5`) | `intValue()`로 소수점을 버림 | 도구 실행 오류 `isError` + `[입력 오류]` |
| 없는 도구 | `-32602` | `-32602` (같음) |
| 도구 결과 | Lombok `toString()` 문자열 | REST와 같은 camelCase JSON + `structuredContent` |
| 지원 프로토콜 | `2025-06-18` 고정 응답 | SDK가 협상 |
| 엔드포인트 | `/mcp`, `/mcp/`, `/mcp/mcp` | `/mcp`, `/mcp/` |

- **외부 API 실패를 `-32603`에서 도구 실행 오류로 바꾼 이유:** MCP 규격은 "API failures"를 도구 실행 오류(`isError: true`)로
  분류합니다. 프로토콜 오류는 요청 구조 자체의 문제(없는 도구, 잘못된 요청 형식)에만 씁니다.
  도구 실행 오류는 LLM에 전달되므로, 모델이 원인(일시적/설정 문제/열차 없음)에 맞게 사용자에게 상황을 설명할 수 있습니다.
- **SDK 기본값에서 바꾼 점:** `mcp` 2.3.0은 없는 도구도 `isError` 결과로 돌려줍니다.
  규격에 맞추려고 이 경우만 JSON-RPC `-32602`로 냅니다(`DepartureMCPServer.call_tool`).
  또 SDK는 도구 오류 메시지 앞에 `Error executing tool …:`를 붙이므로, 접두어(`[입력 오류]`, `[판단 불가]`)가
  맨 앞에 오도록 결과를 직접 만듭니다.
