# 이 파일이 확인하는 것: AI 모델 고르기 규칙(Gemini 무료 API 먼저 → 한도가 차면 학교 서버 Ollama qwen2.5).
# - Gemini·Ollama에 요청을 올바른 모양으로 보내는지, 실패 종류(한도 소진/일반 실패)를 구분하는지 본다.
# - 한도가 차면 일정 시간(쿨다운) 동안 Gemini를 쉬고 Ollama로 답하는지 본다.
# 실제 모델은 부르지 않는다. 'test-key' 같은 키는 진짜 키가 아닌 임의 글자다.

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

# 테스트에 쓰는 사용자 질문 한 개.
MSGS = [Message("user", "반납은 어떻게 해요?")]


# Gemini가 정상으로 답할 때의 응답 모양.
def gemini_ok(text: str = "부스에서 다시 찍어요") -> dict:
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]}


# Gemini 무료 한도가 찼을 때의 응답 모양(RESOURCE_EXHAUSTED, "몇 초 뒤 다시"를 담은 retryDelay).
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


# 시간을 마음대로 흘릴 수 있는 가짜 시계(쿨다운 확인용).
class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


# 가짜 모델. 정해 둔 결과(답 글자나 오류)를 차례로 돌려주고, 몇 번 불렸는지(calls) 센다.
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


# [상황] Gemini에 대화(사용자→챗봇→사용자)를 보낸다.
# [기대] API 키는 주소에 넣지 않고 헤더에만 넣는다(로그에 키가 남지 않게).
#        챗봇 안내문은 systemInstruction 칸에, 챗봇 역할 이름은 Gemini가 쓰는 model로 바꿔 보낸다.
def test_gemini_요청에_키는_헤더로만_보내고_시스템_지시와_역할을_바꿔_넣는다():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["key"] = req.headers.get("x-goog-api-key")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=gemini_ok())

    g = GeminiClient(client(handler), "test-key", "gemini-3.5-flash", "https://g.example/v1beta", 5)
    out = g.generate("SYS", [Message("user", "q"), Message("assistant", "a"), Message("user", "q2")])
    assert out == "부스에서 다시 찍어요"
    assert seen["url"] == "https://g.example/v1beta/models/gemini-3.5-flash:generateContent"
    assert "test-key" not in seen["url"]
    assert seen["key"] == "test-key"
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "SYS"
    assert [c["role"] for c in seen["body"]["contents"]] == ["user", "model", "user"]


# [상황] "생각 끄기(thinkingConfig)" 옵션을 받지 않는 Gemini 모델이 400으로 거절한다.
# [기대] 그 옵션을 빼고 한 번 더 보내 답을 받고, 다음 질문부터는 처음부터 빼고 보낸다.
def test_gemini_생각_끄기를_거부하는_모델이면_빼고_다시_보내고_그_뒤로도_뺀다():
    bodies = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        bodies.append(body)
        if "thinkingConfig" in body["generationConfig"]:
            return httpx.Response(400, json={"error": {"code": 400, "status": "INVALID_ARGUMENT"}})
        return httpx.Response(200, json=gemini_ok("네"))

    g = GeminiClient(client(handler), "k", "gemini-flash-lite-latest", "https://g", 5)
    assert g.generate("S", MSGS) == "네"
    assert g.generate("S", MSGS) == "네"
    assert ["thinkingConfig" in b["generationConfig"] for b in bodies] == [True, False, False]


# [상황] 다른 이유의 400 오류가 온다.
# [기대] 다시 보내지 않고 바로 실패로 본다(요청은 1번만).
def test_gemini_생각_끄기와_상관없는_400은_다시_보내지_않고_실패다():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, json={"error": {"code": 400, "status": "FAILED_PRECONDITION"}})

    g = GeminiClient(client(handler), "k", "m", "https://g", 5)
    with pytest.raises(ProviderError):
        g.generate("S", MSGS)
    assert len(calls) == 1


# [상황] Gemini가 429와 "42초 뒤 다시"(retryDelay 42s)를 돌려준다.
# [기대] "한도 소진" 오류로 구분하고 기다릴 시간 42초를 읽어 둔다.
def test_gemini_429는_한도_소진으로_보고_retryDelay를_읽는다():
    g = GeminiClient(client(lambda r: httpx.Response(429, json=quota_body("42s"))), "k", "m", "https://g", 5)
    with pytest.raises(QuotaExceeded) as e:
        g.generate("S", MSGS)
    assert e.value.retry_after == 42.0


# [상황] 상태 코드는 400인데 내용이 RESOURCE_EXHAUSTED(한도 소진)다.
# [기대] 코드가 달라도 한도 소진으로 본다.
def test_gemini_상태가_RESOURCE_EXHAUSTED면_코드가_달라도_한도_소진이다():
    body = {"error": {"code": 400, "status": "RESOURCE_EXHAUSTED"}}
    g = GeminiClient(client(lambda r: httpx.Response(400, json=body)), "k", "m", "https://g", 5)
    with pytest.raises(QuotaExceeded):
        g.generate("S", MSGS)


# [상황] Gemini가 503(서버 문제)으로 답하거나, 200인데 답이 비어 있다.
# [기대] 한도 소진이 아닌 "일반 실패"로 본다(짧게만 쉬고 다시 시도하게 됨).
def test_gemini_5xx와_빈_답은_일반_실패다():
    g = GeminiClient(client(lambda r: httpx.Response(503, json={})), "k", "m", "https://g", 5)
    with pytest.raises(ProviderError) as e:
        g.generate("S", MSGS)
    assert not isinstance(e.value, QuotaExceeded)
    g2 = GeminiClient(client(lambda r: httpx.Response(200, json={"candidates": []})), "k", "m", "https://g", 5)
    with pytest.raises(ProviderError):
        g2.generate("S", MSGS)


# [상황] Ollama(qwen2.5:3b)에 질문을 보낸다.
# [기대] /api/chat 주소로, 한 번에 받기(stream 끔), 챗봇 안내문을 맨 앞 system 메시지로 붙여 보낸다.
#        답 앞뒤 공백은 정리한다. 스레드 수는 따로 정하지 않았으면 보내지 않는다.
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
    assert "num_thread" not in seen["body"]["options"]


# [상황] Ollama가 쓸 CPU 스레드 수를 2로 정했다(학교 서버 자원 보호).
# [기대] 요청의 options에 num_thread=2를 넣는다.
def test_ollama_스레드_수를_정하면_options로_넘긴다():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "네"}})

    OllamaClient(client(handler), "http://o", "qwen2.5:3b", 5, num_thread=2).generate("S", MSGS)
    assert seen["body"]["options"]["num_thread"] == 2


# [상황] 학교 서버 Ollama에 qwen2.5:3b 모델이 아직 내려받아져 있지 않다(404).
# [기대] 실패 메시지에 "ollama pull qwen2.5:3b"를 실행하라는 안내가 들어간다.
def test_ollama_모델이_없으면_pull_안내가_담긴_실패다():
    http = client(lambda r: httpx.Response(404, json={"error": "model not found"}))
    o = OllamaClient(http, "http://o", "qwen2.5:3b", 5)
    with pytest.raises(ProviderError, match="ollama pull qwen2.5:3b"):
        o.generate("S", MSGS)


# [상황] Gemini가 정상 답한다.
# [기대] Gemini 답을 쓰고, 어느 모델이 답했는지 gemini로 표시한다.
def test_gemini가_답하면_gemini_답을_쓴다():
    r = ChatRouter(Fake("G"), Fake("O"), 300, 30, FakeClock())
    assert (r.reply("S", MSGS).text, r.reply("S", MSGS).provider) == ("G", "gemini")


# [상황] Gemini 무료 한도가 찼다(쿨다운 300초).
# [기대] 그 질문은 Ollama가 답한다. 299초 뒤에도 Gemini를 부르지 않고 Ollama로 답하며,
#        300초가 지나면 다시 Gemini로 돌아간다.
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


# [상황] Gemini가 "3600초 뒤 다시"라고 했는데 기본 쿨다운은 300초다.
# [기대] 더 긴 쪽(3600초)을 따른다. 1000초 뒤에도 여전히 Ollama로 답한다.
def test_retryDelay가_쿨다운보다_길면_더_오래_기다린다():
    clock = FakeClock()
    g = Fake(QuotaExceeded("q", retry_after=3600), "G")
    r = ChatRouter(g, Fake("O"), 300, 30, clock)
    r.reply("S", MSGS)
    clock.t += 1000
    assert r.reply("S", MSGS).provider == "ollama"
    assert g.calls == 1


# [상황] Gemini가 한도와 무관하게 잠깐 실패한다.
# [기대] 그 질문은 Ollama가 답하고, 짧은 대기(30초) 뒤에는 다시 Gemini를 쓴다.
def test_gemini_일반_실패는_짧게만_건너뛴다():
    clock = FakeClock()
    g = Fake(ProviderError("down"), "G")
    r = ChatRouter(g, Fake("O"), 300, 30, clock)
    assert r.reply("S", MSGS).provider == "ollama"
    clock.t += 31
    assert r.reply("S", MSGS).provider == "gemini"


# [상황] Gemini API 키를 설정하지 않았다.
# [기대] Gemini는 건너뛰고 바로 Ollama로 답한다.
def test_키가_없으면_바로_ollama로_답한다():
    r = ChatRouter(None, Fake("O"), 300, 30, FakeClock())
    assert r.reply("S", MSGS).provider == "ollama"


# [상황] Gemini는 한도 소진, Ollama도 실패한다.
# [기대] "모든 모델 실패" 오류를 낸다(API에서는 503이 된다).
def test_둘_다_실패하면_AllProvidersFailed():
    r = ChatRouter(Fake(QuotaExceeded("q")), Fake(ProviderError("x")), 300, 30, FakeClock())
    with pytest.raises(AllProvidersFailed):
        r.reply("S", MSGS)
