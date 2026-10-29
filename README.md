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
| `direction` | string | `상행`/`하행` (2호선은 `내선`/`외선`, 생략 가능) |
| `estimatedTimeToStation` | int | 역까지 이동 시간(분) |
| `presetName` | string | 저장된 프리셋. 있으면 `estimatedTimeToStation`보다 우선 |
| `currentTime` | `HH:mm` | 현재 시각 (생략하면 서버의 한국 시각) |

```bash
curl -X POST http://localhost:8000/departure/assess \
  -H 'Content-Type: application/json' \
  -d '{"station": "신대방역", "line": "2호선", "direction": "내선", "presetName": "home"}'
```

```json
{"decision": "WAIT", "recommendedDepartureTime": "08:35", "reason": "지금 출발하면 역에서 약 5분 대기합니다. 08:35에 출발을 추천합니다."}
```

| `decision` | 의미 | `recommendedDepartureTime` |
|---|---|---|
| `GO_NOW` | 지금 출발하면 대기 없이 탑승 | `"NOW"` |
| `WAIT` | 추천 시각에 출발 | `"HH:mm"` |
| `TOO_LATE` | 도착 예정 열차로는 탑승이 어려움 | `null` |

### 오류 응답

판단할 수 없는 경우에는 판단 결과 대신 오류를 돌려줍니다.

```json
// 400 입력 오류: 역/호선 누락, 모르는 호선, 이동 시간 누락·0 이하, 없는 프리셋, 잘못된 형식
{"error": "INVALID_INPUT", "message": "지원하지 않는 호선입니다: 100호선"}

// 502 판단 불가: 외부 API 실패, 빈 응답, API 오류 코드
{"error": "ARRIVAL_UNAVAILABLE", "message": "판단할 수 없습니다: 도착 정보 API 호출에 실패했습니다 (3회 시도, 마지막 사유: HTTP 503)."}
```

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

| 도구 | 입력 | 필수 | 결과 |
|---|---|---|---|
| `assess_departure_timing` | `station`, `line`, `estimatedTimeToStation`(number), `direction`, `presetName` | `station`, `line` | `{decision, recommendedDepartureTime, reason}` |
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
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"assess_departure_timing","arguments":{"station":"신대방역","line":"2호선","estimatedTimeToStation":10}}}'
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
| 외부 API 실패(재시도 후), 빈 응답, API 오류 코드 | 도구 실행 오류 | `isError: true`, `"[판단 불가] … 잠시 후 다시 시도해 주세요."` |

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
| 재시도 대상 | 타임아웃, 연결 실패, HTTP 5xx, HTTP 429 |
| 재시도 안 함 | 그 밖의 4xx, 응답 본문의 오류 코드, 정상 응답이지만 열차 정보 없음 |
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
  이 경우에도 오류 코드(`INFO-100` 등)를 읽어 `502` 메시지에 담습니다.
- **기준 시각:** `currentTime`을 생략하면 서버 시간대와 무관하게 한국 시각을 씁니다(Java는 서버 로컬 시각).
- **방향 검증:** Java는 2호선에 상행/하행이, 다른 호선에 내선/외선이 들어오면 방향 필터를 끄고 양방향 열차를 섞어 판단했으므로, 이 버전은 호선과 맞지 않는 방향을 입력 오류(REST `400`, MCP `[입력 오류]`)로 알립니다(상행↔내선 같은 변환은 하지 않음).

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
  도구 실행 오류는 LLM에 전달되므로, 모델이 "잠시 후 다시 시도"처럼 사용자에게 상황을 설명할 수 있습니다.
- **SDK 기본값에서 바꾼 점:** `mcp` 2.3.0은 없는 도구도 `isError` 결과로 돌려줍니다.
  규격에 맞추려고 이 경우만 JSON-RPC `-32602`로 냅니다(`DepartureMCPServer.call_tool`).
  또 SDK는 도구 오류 메시지 앞에 `Error executing tool …:`를 붙이므로, 접두어(`[입력 오류]`, `[판단 불가]`)가
  맨 앞에 오도록 결과를 직접 만듭니다.
