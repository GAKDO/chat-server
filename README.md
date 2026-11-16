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
- 외부 모델(Gemini)로 보내는 [내 정보]에는 기기 일련번호를 **뒤 4자리만** 넣는다(`mask_serial`). 테스트가 모델이 받은 요청에 전체 일련번호가 0건인지 확인한다.
- 내 정보를 읽어 답했을 때만 답 끝에 `(TRACE 기록 HH:MM 조회 기준)`을 코드가 붙인다(한국 시간). 시각은 모델이 아니라 서버가 채운다.

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
| `OLLAMA_NUM_THREAD` | `0` | Ollama CPU 스레드 수. compose의 ollama `cpus`와 맞춘다(0이면 서버 코어 수만큼 띄워 CPU 제한 안에서 서로 다툰다) |

## 학교 서버 Compose 구성 (배포됨)

11/10부터 https://gakdo.site/ 학교 서버 Compose에서 돌고 있다. 구성 원본은 gitops `compose/docker-compose.yml`의 `chat-server`·`ollama` 서비스이고(gitops는 A 담당), 여기에는 요점만 적는다.

- `chat-server`: 이미지 `ghcr.io/gakdo/chat-server:develop`, 메모리 256MB, gateway 뒤에서만 받는다(포트를 밖에 열지 않음). 위 환경 변수를 compose에서 넘기고 `GEMINI_API_KEY`는 서버 `.env`에만 둔다
- `ollama`: `ollama/ollama` 0.12.6, 서버에 우리 것이 없어서 같이 띄운다. 메모리 4GB·CPU 2개로 제한(다른 팀과 같이 쓰는 서버), 모델을 24시간 메모리에 둔다(`OLLAMA_KEEP_ALIVE`). 포트는 밖으로 열지 않는다
- 배포: develop에 머지하면 GitHub Actions가 `:develop` 이미지를 올리고, 학교 서버의 자동 배포(gitops `compose/ops/autodeploy.sh`, 3분마다)가 받아 바꾼다. 바깥 점검(gitops "운영 점검")이 30분마다 `/api/v1/chat/ping`을 본다
- 화면: web 오른쪽 아래 챗봇 창이 `POST /api/v1/chat`을 부른다

처음 한 번 모델을 받는다: `docker compose exec ollama ollama pull qwen2.5:3b` (약 2GB).

## 개발

```bash
python -m pip install -e ".[dev]"
ruff check . && pytest -q
GEMINI_API_KEY=... OLLAMA_BASE_URL=http://localhost:11434 TRACE_API_URL=https://gakdo.site trace-chat
```

테스트는 Gemini·Ollama·gateway를 httpx `MockTransport`로 바꿔 끼우므로 키나 네트워크 없이 돈다.

## 실제 IP와 요청 제한

사용자 → 학교 프록시(nginx, `220.67.216.12`) → gateway → chat-server 순서로 들어온다.
gateway(Spring Cloud Gateway 4.3)는 `trusted-proxies`를 설정하지 않아 들어온 `X-Forwarded-*`를 지우므로, 보통은 학교 프록시가 넣은 `X-Real-IP`를 쓴다(gateway 로그인 제한과 같은 헤더).
`X-Forwarded-For`가 오면 오른쪽부터 읽으며 `CHAT_TRUSTED_PROXIES`에 든 주소를 건너뛰고 처음 나오는 주소를 사용자 IP로 본다. 왼쪽 값은 사용자가 꾸밀 수 있어 쓰지 않는다.
사용자 IP 자리에 프록시 주소가 오면(앞단이 `X-Real-IP`를 넘기지 않음) 로그에 경고를 한 번 남긴다. 카운터는 메모리에 있어 chat-server를 다시 시작하면 처음부터 센다.
