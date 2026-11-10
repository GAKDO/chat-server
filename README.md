# chat-server

TRACE 사이트(https://gakdo.site/)의 도우미 챗봇 서버입니다. 사이트 사용자가 자연어로 묻는 질문에 답합니다.

- 사이트 사용법: "반납은 어떻게 해요?", "등급 B는 뭐예요?", "QR을 찍었는데 렌탈이 안 보여요" (로그인 없이)
- 내 정보: "내 렌탈 상태 알려줘", "출고 검사 결과 어땠어?" (로그인한 사용자. 본인 토큰으로 기존 API를 **읽기만** 한다)

## 모델 순서

1. **Gemini**(Google AI Studio 무료 API, 기본 `gemini-3.5-flash`)
2. Gemini가 한도를 알리면(HTTP 429 또는 `RESOURCE_EXHAUSTED`) **Ollama**(`qwen2.5:3b`)로 답한다.
   그 뒤 `GEMINI_COOLDOWN_SECONDS`(기본 300초, Gemini가 알려 준 `retryDelay`가 더 길면 그만큼) 동안은 Gemini를 부르지 않고 바로 Ollama로 간다.
3. Gemini가 한도 말고 다른 이유(5xx·연결 실패)로 실패하면 30초만 건너뛴다.
4. `GEMINI_API_KEY`가 비어 있으면 처음부터 Ollama로만 답한다.

응답의 `provider`(`gemini`·`ollama`)로 어느 모델이 답했는지 알 수 있다.

## API

gateway가 `/api/v1/chat/**`를 이 서버(8080)로 넘긴다.

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/api/v1/chat/ping` | 배포 확인(로그인 없이). `{"service":"chat-server","version":"0.1.0","commit":"abc1234"}` |
| POST | `/api/v1/chat` | 질문 → 답. 로그인 없이 열고, `Authorization: Bearer <토큰>`이 있으면 내 정보도 읽는다 |

```http
POST /api/v1/chat
{"messages": [{"role": "user", "content": "반납은 어떻게 해요?"}]}

200 {"reply": "반납은 ...", "provider": "gemini"}
```

- `messages`: 최근 대화(최대 40개, 한 개 2000자). 마지막은 `user`여야 한다. 서버는 대화를 저장하지 않고, 모델에는 최근 10개만 보낸다.
- 오류는 Problem Details(`application/problem+json`): 400 `VALIDATION_FAILED`, 429 `RATE_LIMITED`(IP당 1분 20개, IP당 하루 100개, 전체 하루 1000개. `detail`에 안내 문구), 503 `INTERNAL_ERROR`(두 모델 모두 실패).
- 내 정보는 질문에 렌탈·검사·등급·반납 같은 말이 있을 때만 읽는다(`GET /api/v1/rentals`, `/api/v1/rentals/{id}`, `/api/v1/captures/checkpoints/{id}/inspection`). 같은 토큰은 30초 캐시한다.

## 환경 변수

| 이름 | 기본값 | 설명 |
| --- | --- | --- |
| `GEMINI_API_KEY` | (없음) | AI Studio에서 받은 키. **trace-secrets·.env에만** 두고 코드·채팅에 적지 않는다 |
| `GEMINI_MODEL` | `gemini-3.5-flash` | |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | 이미 떠 있는 Ollama가 있으면 그 주소 |
| `OLLAMA_MODEL` | `qwen2.5:3b` | CPU 서버 기준. GPU가 있으면 `qwen2.5:7b` |
| `TRACE_API_URL` | `http://gateway:8080` | 내 정보를 읽을 API 입구 |
| `GEMINI_COOLDOWN_SECONDS` | `300` | 한도 소진 뒤 Gemini를 건너뛰는 시간 |
| `CHAT_RATE_LIMIT_PER_MINUTE` | `20` | IP당 1분 질문 수. 토큰은 검증하지 않으므로 제한 기준으로 쓰지 않는다 |
| `CHAT_DAILY_LIMIT_PER_IP` | `100` | IP당 하루 질문 수(한국 시간 자정에 초기화, 0이면 끔) |
| `CHAT_DAILY_LIMIT_TOTAL` | `1000` | 서비스 전체 하루 질문 수(Gemini 사용량 상한, 0이면 끔) |
| `CHAT_TRUSTED_PROXIES` | 학교 프록시 `220.67.216.12` + 사설·도커 대역 | `X-Forwarded-For`를 오른쪽부터 읽으며 건너뛸 프록시(IP·CIDR, 쉼표) |
| `OLLAMA_TIMEOUT_SECONDS` | `120` | |

## 학교 서버 Compose에 붙이기

gitops `compose/docker-compose.yml`에 아래 두 서비스를 더한다(gitops는 A 담당이라 A가 반영). Ollama는 서버에 우리 것이 없어서 같이 띄운다.

```yaml
  chat-server:
    platform: linux/amd64
    image: ghcr.io/gakdo/chat-server:${CHAT_SERVER_TAG:-${TRACE_TAG:-develop}}
    restart: unless-stopped
    mem_limit: 256m
    depends_on: [ollama]
    environment:
      GEMINI_API_KEY: ${GEMINI_API_KEY:-}
      OLLAMA_BASE_URL: ${OLLAMA_BASE_URL:-http://ollama:11434}
      OLLAMA_MODEL: ${OLLAMA_MODEL:-qwen2.5:3b}
      TRACE_API_URL: http://gateway:8080

  ollama:
    image: ollama/ollama:0.12.6
    restart: unless-stopped
    volumes: [ollama-data:/root/.ollama]
    mem_limit: 8g          # 다른 팀과 같이 쓰는 서버라 메모리를 제한한다
    # 포트는 밖으로 열지 않는다(chat-server만 같은 네트워크에서 부른다)
```

처음 한 번 모델을 받는다: `docker compose exec ollama ollama pull qwen2.5:3b` (약 2GB).

## 개발

```bash
python -m pip install -e ".[dev]"
ruff check . && pytest -q
GEMINI_API_KEY=... OLLAMA_BASE_URL=http://localhost:11434 TRACE_API_URL=https://gakdo.site trace-chat
```

테스트는 Gemini·Ollama·gateway를 httpx `MockTransport`로 바꿔 끼우므로 키나 네트워크 없이 돈다.

## 실제 IP와 요청 제한

사용자 → 학교 프록시(nginx, `220.67.216.12`) → gateway → chat-server 순서로 들어온다. gateway가 `X-Forwarded-For` 끝에 프록시 주소를 붙여 넘기므로,
오른쪽부터 읽으며 `CHAT_TRUSTED_PROXIES`에 든 주소를 건너뛰고 처음 나오는 주소를 사용자 IP로 본다. 왼쪽 값은 사용자가 꾸밀 수 있어 쓰지 않는다.
`X-Forwarded-For`가 프록시 주소뿐이면 `X-Real-IP`를 쓴다. 카운터는 메모리에 있어 chat-server를 다시 시작하면 처음부터 센다.
