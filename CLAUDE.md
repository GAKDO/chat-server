# chat-server

TRACE의 사이트 도우미 챗봇(Gemini 무료 API → 한도 소진 시 Ollama qwen2.5) 레포입니다. 담당: B.

## 먼저 읽을 것

- `README.md`: 모델 전환 규칙, API, 환경 변수, Compose에 붙이는 법
- 계약 원본: [GAKDO/contracts](https://github.com/GAKDO/contracts) (OpenAPI·`codes.yaml`). 오류 코드는 codes.yaml의 `error_code`만 쓴다
- 사이트 안내 원문: GAKDO/web `src/features/guide/content.ts` (바뀌면 `src/trace_chat/knowledge.py`도 고친다)

## 명령

- 설치: `python -m pip install -e ".[dev]"`
- 검사: `ruff check . && pytest -q`
- 실행: `trace-chat` (8080)

## 코드 컨벤션 요약

- `src/trace_chat/`, 테스트는 `tests/`. 외부 호출(Gemini·Ollama·gateway)은 httpx.Client 하나로 감싸 테스트에서 MockTransport로 바꿔 끼움.
- ruff 통과, 타입 힌트 필수, 데이터는 dataclass·pydantic.
- 챗봇은 읽기만 한다. 사용자 토큰으로 GET만 부르고, 무언가를 바꾸는 API는 부르지 않는다.
- 커밋 `<종류>(<범위>): <내용>`, PR 하나 = 기능 하나, PR 본문에 테스트 방법.

## 꼭 지킬 것

- 계약(API·이벤트·코드 값)을 바꿔야 하면 코드를 고치지 말고 멈추고 사람에게 알린다.
- API 키·토큰·비밀번호를 코드·문서·커밋·로그에 넣지 않는다. 설정은 환경 변수로 받는다.
- 커밋은 `<종류>(<범위>): <내용>`(feat·fix·refactor·test·docs·chore·ci), 문서·PR·주석은 한국어.
