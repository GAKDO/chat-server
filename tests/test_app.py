# 이 파일이 확인하는 것: 사이트 도우미 챗봇의 HTTP 창구(POST /api/v1/chat)
# - 사용자가 챗봇 창에 질문을 보내면 어떤 답(상태 코드·본문)이 돌아오는지 본다.
# - 로그인한 사용자가 "내 렌탈" 같은 질문을 하면 그 사람의 렌탈 기록을 읽어 답에 보태는지 본다.
# - 남용을 막는 요청 제한(1분당·하루당)과, 외부 AI 모델로 일련번호 전체가 새지 않는지 본다.
# 실제 AI 모델(Gemini·Ollama)과 TRACE 서버는 부르지 않고 아래의 가짜(Fake)로 바꿔 끼운다.

"""HTTP API."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from trace_chat.app import create_app
from trace_chat.config import Settings
from trace_chat.llm import AllProvidersFailed, Reply
from trace_chat.ratelimit import DailyLimiter, RateLimiter


# 가짜 AI 모델 묶음. 진짜 Gemini·Ollama 대신 항상 "답변"이라고만 답하고,
# 무엇을 물어봤는지(calls)를 기록해 둔다. fail=True면 "모델이 모두 실패" 상황을 흉내 낸다.
class FakeRouter:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list = []

    def reply(self, system, messages):
        self.calls.append((system, list(messages)))
        if self.fail:
            raise AllProvidersFailed("x")
        return Reply("답변", "gemini")


# 가짜 TRACE 서버. 이 사용자에게 열린 렌탈 R1(맥북 에어 13 M2, 일련번호 C02FULLSERIAL9)이
# 하나 있는 것처럼 답한다. 어떤 로그인 토큰으로 불렸는지(tokens)를 기록한다.
class FakeApi:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    def my_rentals(self, token):
        self.tokens.append(token)
        return [{"rentalId": "R1", "status": "OPEN", "modelCode": "MBA13-M2", "serial": "C02FULLSERIAL9"}]

    def rental(self, token, rental_id):
        return {"checkpoints": []}

    def inspection(self, token, checkpoint_id):
        return None


# 테스트에서 "지금 시각"을 고정한다(한국 시간 14시 03분). 답 끝의 조회 시각 꼬리말을 확인할 때 쓴다.
FIXED_NOW = datetime(2026, 11, 12, 5, 3, tzinfo=UTC)  # KST 14:03


# 테스트용 챗봇 서버를 만든다. limit = 1분에 받을 질문 수, daily = 하루 상한(0이면 상한 없음).
def make(router=None, api=None, limit: int = 20, daily: DailyLimiter | None = None):
    router = router or FakeRouter()
    api = api or FakeApi()
    app = create_app(Settings(app_commit="abcdef123"), router=router, api=api, limiter=RateLimiter(limit),
                     daily=daily or DailyLimiter(0, 0), clock=lambda: FIXED_NOW)
    return TestClient(app), router, api


# 챗봇 창에서 질문 하나를 보내는 동작. token을 주면 로그인한 사용자, ip를 주면 그 IP에서 보낸 것.
def ask(c: TestClient, text: str, token: str | None = None, history: list | None = None, ip: str | None = None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if ip:
        # 학교 프록시가 넣은 사용자 IP를 gateway가 그대로 넘기는 모양
        headers["X-Real-IP"] = ip
    return c.post("/api/v1/chat", json={"messages": (history or []) + [{"role": "user", "content": text}]},
                  headers=headers)


# [상황] 운영 점검용 주소(/api/v1/chat/ping)를 부른다.
# [기대] 서비스 이름 chat-server, 버전, 배포된 커밋 앞 7자리를 돌려준다(어떤 버전이 떠 있는지 확인용).
def test_ping은_서비스_이름과_커밋을_준다():
    c, _, _ = make()
    assert c.get("/api/v1/chat/ping").json() == {"service": "chat-server", "version": "0.1.0", "commit": "abcdef1"}


# [상황] 로그인하지 않은 방문자가 "내 렌탈 상태 알려줘"라고 묻는다.
# [기대] 정상 답(200)을 준다. 다만 누구인지 모르므로 TRACE 서버에서 개인 기록을 읽지 않고,
#        AI에게 주는 안내문에도 [내 정보] 칸이 들어가지 않는다(사이트 사용법만 안내).
#        ※ 응답 코드: 200 = 정상 처리됨
def test_로그인_없이도_사이트_사용법을_답한다():
    c, router, api = make()
    r = ask(c, "내 렌탈 상태 알려줘")
    assert r.status_code == 200
    assert r.json() == {"reply": "답변", "provider": "gemini"}
    assert api.tokens == []
    assert "[내 정보]\n" not in router.calls[0][0]


# [상황] 로그인한 사용자가 "내 렌탈 상태 알려줘"라고 묻는다.
# [기대] 그 사람의 토큰으로 TRACE 서버에서 렌탈 기록을 읽고, AI에게 주는 안내문에
#        [내 정보]와 "렌탈 R1"을 넣어 그 사람 상황에 맞게 답하게 한다.
def test_로그인한_사용자가_렌탈을_물으면_내_정보를_붙인다():
    c, router, api = make()
    ask(c, "내 렌탈 상태 알려줘", token="tok")
    assert api.tokens == ["tok"]
    assert "[내 정보]\n" in router.calls[0][0] and "렌탈 R1" in router.calls[0][0]


# [상황] 로그인한 사용자가 렌탈과 상관없는 질문("회원가입은 어떻게 해요?")을 한다.
# [기대] 필요 없는 개인 기록은 읽지 않는다(TRACE 서버를 한 번도 부르지 않음).
def test_렌탈과_관계없는_질문에는_내_정보를_읽지_않는다():
    c, router, api = make()
    ask(c, "회원가입은 어떻게 해요?", token="tok")
    assert api.tokens == []


# [상황] 앞서 질문·답이 20개(10번 왕복) 쌓인 채로 새 질문을 보낸다.
# [기대] AI에게는 최근 10개까지만 보내고(비용·속도), 그 묶음은 사용자 질문으로 시작해
#        방금 보낸 "마지막" 질문으로 끝난다.
def test_지난_대화는_최근_10개만_보내고_사용자_질문으로_시작한다():
    c, router, _ = make()
    history = []
    for i in range(10):
        history += [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": f"a{i}"}]
    ask(c, "마지막", history=history)
    sent = router.calls[0][1]
    assert len(sent) <= 10 and sent[0].role == "user" and sent[-1].content == "마지막"


# [상황] 질문이 비어 있거나, 사용자 질문 없이 챗봇 답만 있거나, 질문이 2000자를 넘는다.
# [기대] 모두 400 VALIDATION_FAILED(계약의 오류 형식 application/problem+json)로 거절한다.
#        ※ 응답 코드: 400 = 요청 내용이 형식에 맞지 않음
def test_형식이_틀리면_400_VALIDATION_FAILED():
    c, _, _ = make()
    r = c.post("/api/v1/chat", json={"messages": []})
    assert r.status_code == 400 and r.json()["code"] == "VALIDATION_FAILED"
    assert r.headers["content-type"].startswith("application/problem+json")
    r = c.post("/api/v1/chat", json={"messages": [{"role": "assistant", "content": "x"}]})
    assert r.status_code == 400
    r = c.post("/api/v1/chat", json={"messages": [{"role": "user", "content": "x" * 2001}]})
    assert r.status_code == 400


# [상황] 1분에 2번까지만 허용한 상태에서 같은 사람이 3번 묻는다.
# [기대] 3번째는 429 RATE_LIMITED와 "1분" 안내. 다른 IP에서 온 질문은 따로 세어 계속 받는다.
#        ※ 응답 코드: 429 = 요청이 너무 잦아 잠시 막음
def test_1분에_정한_수를_넘으면_429_RATE_LIMITED():
    c, _, _ = make(limit=2)
    assert ask(c, "a").status_code == 200
    assert ask(c, "b").status_code == 200
    r = ask(c, "c")
    assert r.status_code == 429 and r.json()["code"] == "RATE_LIMITED"
    assert "1분" in r.json()["detail"]
    # 다른 IP는 따로 센다
    assert ask(c, "d", ip="203.0.113.7").status_code == 200


# [상황] 같은 IP에서 로그인 토큰을 매번 다른 가짜 값으로 바꿔 보내며 제한을 피하려 한다.
# [기대] 제한은 IP 기준이라 토큰을 바꿔도 3번째부터 429로 막힌다.
#        ※ 응답 코드: 429 = 요청이 너무 잦아 잠시 막음
def test_가짜_토큰을_바꿔_넣어도_같은_IP면_분당_제한을_피하지_못한다():
    c, _, _ = make(limit=2)
    assert ask(c, "a", token="fake-1", ip="203.0.113.7").status_code == 200
    assert ask(c, "b", token="fake-2", ip="203.0.113.7").status_code == 200
    assert ask(c, "c", token="fake-3", ip="203.0.113.7").status_code == 429
    assert ask(c, "d", ip="203.0.113.7").status_code == 429


# [상황] 누군가 X-Forwarded-For 헤더 맨 앞에 가짜 IP(1.1.1.1, 9.9.9.9)를 끼워 다른 사람인 척한다.
# [기대] 학교 프록시(220.67.216.12) 바로 앞의 진짜 주소(203.0.113.7)로 세므로 2번째에 429.
#        ※ 응답 코드: 429 = 요청이 너무 잦아 잠시 막음
def test_gateway가_X_Forwarded_For를_넘기게_바뀌어도_앞쪽을_꾸민_값으로는_피하지_못한다():
    c, _, _ = make(limit=1)
    spoof = {"X-Forwarded-For": "1.1.1.1, 203.0.113.7, 220.67.216.12"}
    msg = {"messages": [{"role": "user", "content": "a"}]}
    assert c.post("/api/v1/chat", json=msg, headers=spoof).status_code == 200
    spoof["X-Forwarded-For"] = "9.9.9.9, 203.0.113.7, 220.67.216.12"
    assert c.post("/api/v1/chat", json=msg, headers=spoof).status_code == 429


# [상황] 한 IP의 하루 상한을 2번으로 두고 같은 IP에서 3번 묻는다.
# [기대] 3번째는 429와 "내일" 다시 오라는 안내, AI 모델은 2번만 불린다(비용 보호).
#        다른 IP는 영향 없이 답을 받는다.
#        ※ 응답 코드: 429 = 요청이 너무 잦아 잠시 막음
def test_IP당_하루_상한을_넘으면_429와_내일_안내():
    c, router, _ = make(daily=DailyLimiter(per_ip=2, total=0))
    assert ask(c, "a", ip="203.0.113.7").status_code == 200
    assert ask(c, "b", ip="203.0.113.7").status_code == 200
    r = ask(c, "c", ip="203.0.113.7")
    assert r.status_code == 429 and r.json()["code"] == "RATE_LIMITED"
    assert "내일" in r.json()["detail"]
    assert len(router.calls) == 2
    assert ask(c, "d", ip="198.51.100.9").status_code == 200


# [상황] 서비스 전체 하루 상한을 2번으로 두고 서로 다른 IP 3곳에서 묻는다.
# [기대] 3번째는 IP가 달라도 429와 "사용량" 안내. 무료 API 한도를 다 쓰지 않게 막는 장치.
#        ※ 응답 코드: 429 = 요청이 너무 잦아 잠시 막음
def test_서비스_전체_하루_상한을_넘으면_모든_IP가_429():
    c, router, _ = make(daily=DailyLimiter(per_ip=0, total=2))
    assert ask(c, "a", ip="203.0.113.7").status_code == 200
    assert ask(c, "b", ip="198.51.100.9").status_code == 200
    r = ask(c, "c", ip="192.0.2.1")
    assert r.status_code == 429 and "사용량" in r.json()["detail"]
    assert len(router.calls) == 2


# [상황] Gemini도 Ollama도 답하지 못한다.
# [기대] 503과 INTERNAL_ERROR 코드로 "지금은 답할 수 없음"을 알린다.
#        ※ 응답 코드: 503 = 서버를 지금 쓸 수 없음(점검·장애)
def test_모델이_모두_실패하면_503():
    c, _, _ = make(router=FakeRouter(fail=True))
    r = ask(c, "안녕")
    assert r.status_code == 503 and r.json()["code"] == "INTERNAL_ERROR"


# [상황] AI가 ##, **굵게**, * 목록, [링크](주소), `코드` 같은 마크다운 기호를 섞어 답한다.
# [기대] 챗봇 창은 일반 글로 보여 주므로 기호를 지우고 목록은 "- "로 바꾼다.
#        AI에게 주는 안내문에도 "마크다운을 쓰지 말라"는 말이 들어 있다.
def test_모델이_쓴_마크다운_기호는_지우고_일반_글로_답한다():
    class MdRouter(FakeRouter):
        def reply(self, system, messages):
            self.calls.append((system, list(messages)))
            text = "## 안내\n노트북 결제는 **렌탈 창구**에서 해요.\n* [회원가입](/signup) 먼저\n`QR`을 스캔"
            return Reply(text, "gemini")

    c, router, _ = make(router=MdRouter())
    r = ask(c, "어떻게 빌려요?")
    assert r.json()["reply"] == "안내\n노트북 결제는 렌탈 창구에서 해요.\n- 회원가입(/signup) 먼저\nQR을 스캔"
    assert "마크다운" in router.calls[0][0]


# [상황] 로그인한 사용자가 자기 렌탈을 묻는다(렌탈 기기 일련번호 C02FULLSERIAL9).
# [기대] 외부 AI로 보내는 글 어디에도 일련번호 전체는 없고 "뒤 4자리 IAL9"만 있다(개인정보 보호).
def test_모델이_받은_요청에는_일련번호_전체가_0건이다():
    c, router, _ = make()
    ask(c, "내 렌탈 상태 알려줘", token="tok")
    sent = router.calls[0][0] + "".join(m.content for m in router.calls[0][1])
    assert "C02FULLSERIAL9" not in sent
    assert "뒤 4자리 IAL9" in sent


# [상황] 로그인한 사용자의 렌탈 기록을 읽어 답했다.
# [기대] 답 끝에 "(TRACE 기록 14:03 조회 기준)" 꼬리말을 붙여 언제 기준 정보인지 알려 준다.
def test_내_기록을_읽어_답하면_코드가_조회_시각_꼬리말을_붙인다():
    c, _, _ = make()
    r = ask(c, "내 렌탈 상태 알려줘", token="tok")
    assert r.json()["reply"] == "답변\n\n(TRACE 기록 14:03 조회 기준)"


# [상황] 렌탈과 무관한 질문이거나, 로그인하지 않아 기록을 읽지 않은 답이다.
# [기대] 조회 시각 꼬리말을 붙이지 않는다.
def test_내_기록을_읽지_않은_답에는_꼬리말이_없다():
    c, _, _ = make()
    assert ask(c, "회원가입은 어떻게 해요?", token="tok").json()["reply"] == "답변"
    assert ask(c, "내 렌탈 상태 알려줘").json()["reply"] == "답변"
