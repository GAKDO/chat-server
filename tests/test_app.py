"""HTTP API."""

from fastapi.testclient import TestClient

from trace_chat.app import create_app
from trace_chat.config import Settings
from trace_chat.llm import AllProvidersFailed, Reply
from trace_chat.ratelimit import DailyLimiter, RateLimiter


class FakeRouter:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list = []

    def reply(self, system, messages):
        self.calls.append((system, list(messages)))
        if self.fail:
            raise AllProvidersFailed("x")
        return Reply("답변", "gemini")


class FakeApi:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    def my_rentals(self, token):
        self.tokens.append(token)
        return [{"rentalId": "R1", "status": "OPEN", "modelCode": "MBA13-M2"}]

    def rental(self, token, rental_id):
        return {"checkpoints": []}

    def inspection(self, token, checkpoint_id):
        return None


def make(router=None, api=None, limit: int = 20, daily: DailyLimiter | None = None):
    router = router or FakeRouter()
    api = api or FakeApi()
    app = create_app(Settings(app_commit="abcdef123"), router=router, api=api, limiter=RateLimiter(limit),
                     daily=daily or DailyLimiter(0, 0))
    return TestClient(app), router, api


def ask(c: TestClient, text: str, token: str | None = None, history: list | None = None, ip: str | None = None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if ip:
        # 학교 프록시가 넣은 사용자 IP를 gateway가 그대로 넘기는 모양
        headers["X-Real-IP"] = ip
    return c.post("/api/v1/chat", json={"messages": (history or []) + [{"role": "user", "content": text}]},
                  headers=headers)


def test_ping은_서비스_이름과_커밋을_준다():
    c, _, _ = make()
    assert c.get("/api/v1/chat/ping").json() == {"service": "chat-server", "version": "0.1.0", "commit": "abcdef1"}


def test_로그인_없이도_사이트_사용법을_답한다():
    c, router, api = make()
    r = ask(c, "내 렌탈 상태 알려줘")
    assert r.status_code == 200
    assert r.json() == {"reply": "답변", "provider": "gemini"}
    assert api.tokens == []
    assert "[내 정보]\n" not in router.calls[0][0]


def test_로그인한_사용자가_렌탈을_물으면_내_정보를_붙인다():
    c, router, api = make()
    ask(c, "내 렌탈 상태 알려줘", token="tok")
    assert api.tokens == ["tok"]
    assert "[내 정보]\n" in router.calls[0][0] and "렌탈 R1" in router.calls[0][0]


def test_렌탈과_관계없는_질문에는_내_정보를_읽지_않는다():
    c, router, api = make()
    ask(c, "회원가입은 어떻게 해요?", token="tok")
    assert api.tokens == []


def test_지난_대화는_최근_10개만_보내고_사용자_질문으로_시작한다():
    c, router, _ = make()
    history = []
    for i in range(10):
        history += [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": f"a{i}"}]
    ask(c, "마지막", history=history)
    sent = router.calls[0][1]
    assert len(sent) <= 10 and sent[0].role == "user" and sent[-1].content == "마지막"


def test_형식이_틀리면_400_VALIDATION_FAILED():
    c, _, _ = make()
    r = c.post("/api/v1/chat", json={"messages": []})
    assert r.status_code == 400 and r.json()["code"] == "VALIDATION_FAILED"
    assert r.headers["content-type"].startswith("application/problem+json")
    r = c.post("/api/v1/chat", json={"messages": [{"role": "assistant", "content": "x"}]})
    assert r.status_code == 400
    r = c.post("/api/v1/chat", json={"messages": [{"role": "user", "content": "x" * 2001}]})
    assert r.status_code == 400


def test_1분에_정한_수를_넘으면_429_RATE_LIMITED():
    c, _, _ = make(limit=2)
    assert ask(c, "a").status_code == 200
    assert ask(c, "b").status_code == 200
    r = ask(c, "c")
    assert r.status_code == 429 and r.json()["code"] == "RATE_LIMITED"
    assert "1분" in r.json()["detail"]
    # 다른 IP는 따로 센다
    assert ask(c, "d", ip="203.0.113.7").status_code == 200


def test_가짜_토큰을_바꿔_넣어도_같은_IP면_분당_제한을_피하지_못한다():
    c, _, _ = make(limit=2)
    assert ask(c, "a", token="fake-1", ip="203.0.113.7").status_code == 200
    assert ask(c, "b", token="fake-2", ip="203.0.113.7").status_code == 200
    assert ask(c, "c", token="fake-3", ip="203.0.113.7").status_code == 429
    assert ask(c, "d", ip="203.0.113.7").status_code == 429


def test_gateway가_X_Forwarded_For를_넘기게_바뀌어도_앞쪽을_꾸민_값으로는_피하지_못한다():
    c, _, _ = make(limit=1)
    spoof = {"X-Forwarded-For": "1.1.1.1, 203.0.113.7, 220.67.216.12"}
    msg = {"messages": [{"role": "user", "content": "a"}]}
    assert c.post("/api/v1/chat", json=msg, headers=spoof).status_code == 200
    spoof["X-Forwarded-For"] = "9.9.9.9, 203.0.113.7, 220.67.216.12"
    assert c.post("/api/v1/chat", json=msg, headers=spoof).status_code == 429


def test_IP당_하루_상한을_넘으면_429와_내일_안내():
    c, router, _ = make(daily=DailyLimiter(per_ip=2, total=0))
    assert ask(c, "a", ip="203.0.113.7").status_code == 200
    assert ask(c, "b", ip="203.0.113.7").status_code == 200
    r = ask(c, "c", ip="203.0.113.7")
    assert r.status_code == 429 and r.json()["code"] == "RATE_LIMITED"
    assert "내일" in r.json()["detail"]
    assert len(router.calls) == 2
    assert ask(c, "d", ip="198.51.100.9").status_code == 200


def test_서비스_전체_하루_상한을_넘으면_모든_IP가_429():
    c, router, _ = make(daily=DailyLimiter(per_ip=0, total=2))
    assert ask(c, "a", ip="203.0.113.7").status_code == 200
    assert ask(c, "b", ip="198.51.100.9").status_code == 200
    r = ask(c, "c", ip="192.0.2.1")
    assert r.status_code == 429 and "사용량" in r.json()["detail"]
    assert len(router.calls) == 2


def test_모델이_모두_실패하면_503():
    c, _, _ = make(router=FakeRouter(fail=True))
    r = ask(c, "안녕")
    assert r.status_code == 503 and r.json()["code"] == "INTERNAL_ERROR"


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
