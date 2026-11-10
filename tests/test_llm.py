"""Gemini → Ollama 전환 규칙."""

import json

import httpx
import pytest

from trace_chat.llm import (
    AllProvidersFailed,
    ChatRouter,
    GeminiClient,
    Message,
    OllamaClient,
    ProviderError,
    QuotaExceeded,
)

MSGS = [Message("user", "반납은 어떻게 해요?")]


def gemini_ok(text: str = "부스에서 다시 찍어요") -> dict:
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]}


def quota_body(delay: str = "37s") -> dict:
    return {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}],
        }
    }


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Fake:
    def __init__(self, *results) -> None:
        self.results = list(results)
        self.calls = 0

    def generate(self, system, messages) -> str:
        self.calls += 1
        r = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(r, Exception):
            raise r
        return r


def test_gemini_요청에_키는_헤더로만_보내고_시스템_지시와_역할을_바꿔_넣는다():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["key"] = req.headers.get("x-goog-api-key")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=gemini_ok())

    g = GeminiClient(client(handler), "test-key", "gemini-2.5-flash", "https://g.example/v1beta", 5)
    out = g.generate("SYS", [Message("user", "q"), Message("assistant", "a"), Message("user", "q2")])
    assert out == "부스에서 다시 찍어요"
    assert seen["url"] == "https://g.example/v1beta/models/gemini-2.5-flash:generateContent"
    assert "test-key" not in seen["url"]
    assert seen["key"] == "test-key"
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "SYS"
    assert [c["role"] for c in seen["body"]["contents"]] == ["user", "model", "user"]


def test_gemini_429는_한도_소진으로_보고_retryDelay를_읽는다():
    g = GeminiClient(client(lambda r: httpx.Response(429, json=quota_body("42s"))), "k", "m", "https://g", 5)
    with pytest.raises(QuotaExceeded) as e:
        g.generate("S", MSGS)
    assert e.value.retry_after == 42.0


def test_gemini_상태가_RESOURCE_EXHAUSTED면_코드가_달라도_한도_소진이다():
    body = {"error": {"code": 400, "status": "RESOURCE_EXHAUSTED"}}
    g = GeminiClient(client(lambda r: httpx.Response(400, json=body)), "k", "m", "https://g", 5)
    with pytest.raises(QuotaExceeded):
        g.generate("S", MSGS)


def test_gemini_5xx와_빈_답은_일반_실패다():
    g = GeminiClient(client(lambda r: httpx.Response(503, json={})), "k", "m", "https://g", 5)
    with pytest.raises(ProviderError) as e:
        g.generate("S", MSGS)
    assert not isinstance(e.value, QuotaExceeded)
    g2 = GeminiClient(client(lambda r: httpx.Response(200, json={"candidates": []})), "k", "m", "https://g", 5)
    with pytest.raises(ProviderError):
        g2.generate("S", MSGS)


def test_ollama는_시스템_메시지를_앞에_붙여_api_chat을_부른다():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": " 안녕하세요 "}})

    o = OllamaClient(client(handler), "http://ollama:11434", "qwen2.5:3b", 5)
    assert o.generate("SYS", MSGS) == "안녕하세요"
    assert seen["path"] == "/api/chat"
    assert seen["body"]["model"] == "qwen2.5:3b"
    assert seen["body"]["stream"] is False
    assert seen["body"]["messages"][0] == {"role": "system", "content": "SYS"}


def test_ollama_모델이_없으면_pull_안내가_담긴_실패다():
    http = client(lambda r: httpx.Response(404, json={"error": "model not found"}))
    o = OllamaClient(http, "http://o", "qwen2.5:3b", 5)
    with pytest.raises(ProviderError, match="ollama pull qwen2.5:3b"):
        o.generate("S", MSGS)


def test_gemini가_답하면_gemini_답을_쓴다():
    r = ChatRouter(Fake("G"), Fake("O"), 300, 30, FakeClock())
    assert (r.reply("S", MSGS).text, r.reply("S", MSGS).provider) == ("G", "gemini")


def test_한도가_차면_ollama로_답하고_쿨다운_동안_gemini를_부르지_않는다():
    clock = FakeClock()
    g, o = Fake(QuotaExceeded("q", retry_after=10), "G"), Fake("O")
    r = ChatRouter(g, o, 300, 30, clock)
    assert r.reply("S", MSGS).provider == "ollama"
    clock.t += 299
    assert r.reply("S", MSGS).provider == "ollama"
    assert g.calls == 1
    clock.t += 2
    assert r.reply("S", MSGS).provider == "gemini"


def test_retryDelay가_쿨다운보다_길면_더_오래_기다린다():
    clock = FakeClock()
    g = Fake(QuotaExceeded("q", retry_after=3600), "G")
    r = ChatRouter(g, Fake("O"), 300, 30, clock)
    r.reply("S", MSGS)
    clock.t += 1000
    assert r.reply("S", MSGS).provider == "ollama"
    assert g.calls == 1


def test_gemini_일반_실패는_짧게만_건너뛴다():
    clock = FakeClock()
    g = Fake(ProviderError("down"), "G")
    r = ChatRouter(g, Fake("O"), 300, 30, clock)
    assert r.reply("S", MSGS).provider == "ollama"
    clock.t += 31
    assert r.reply("S", MSGS).provider == "gemini"


def test_키가_없으면_바로_ollama로_답한다():
    r = ChatRouter(None, Fake("O"), 300, 30, FakeClock())
    assert r.reply("S", MSGS).provider == "ollama"


def test_둘_다_실패하면_AllProvidersFailed():
    r = ChatRouter(Fake(QuotaExceeded("q")), Fake(ProviderError("x")), 300, 30, FakeClock())
    with pytest.raises(AllProvidersFailed):
        r.reply("S", MSGS)
