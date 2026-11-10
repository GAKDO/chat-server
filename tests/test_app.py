"""HTTP API."""

from fastapi.testclient import TestClient

from trace_chat.app import create_app
from trace_chat.config import Settings
from trace_chat.llm import AllProvidersFailed, Reply
from trace_chat.ratelimit import RateLimiter


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


def make(router=None, api=None, limit: int = 20):
    router = router or FakeRouter()
    api = api or FakeApi()
    app = create_app(Settings(app_commit="abcdef123"), router=router, api=api, limiter=RateLimiter(limit))
    return TestClient(app), router, api


def ask(c: TestClient, text: str, token: str | None = None, history: list | None = None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
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
    # 다른 사용자(토큰)는 따로 센다
    assert ask(c, "d", token="tok").status_code == 200


def test_모델이_모두_실패하면_503():
    c, _, _ = make(router=FakeRouter(fail=True))
    r = ask(c, "안녕")
    assert r.status_code == 503 and r.json()["code"] == "INTERNAL_ERROR"
