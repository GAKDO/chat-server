"""언어 모델 호출. Gemini(무료 API)를 먼저 쓰고, 한도를 다 쓰면 Ollama(qwen2.5)로 넘어간다.

외부 호출은 httpx.Client 하나로만 하므로 테스트에서는 MockTransport로 바꿔 끼운다.
API 키는 URL이 아니라 헤더(x-goog-api-key)로 보내 로그·예외 메시지에 남지 않게 한다.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

log = logging.getLogger(__name__)

Role = Literal["user", "assistant"]
ProviderName = Literal["gemini", "ollama"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class Reply:
    text: str
    provider: ProviderName


class ProviderError(Exception):
    """모델이 답하지 못함(연결 실패·5xx·빈 답 등)."""


class QuotaExceeded(ProviderError):
    """무료 한도 소진(HTTP 429 또는 status RESOURCE_EXHAUSTED)."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class AllProvidersFailed(Exception):
    """Gemini와 Ollama 모두 답하지 못함."""


class Generator(Protocol):
    def generate(self, system: str, messages: Sequence[Message]) -> str: ...


_DELAY = re.compile(r"^\s*(\d+(?:\.\d+)?)s\s*$")


def _retry_after(resp: httpx.Response, body: object) -> float | None:
    """Retry-After 헤더나 Gemini 오류 본문의 RetryInfo.retryDelay("37s")에서 기다릴 초를 읽는다."""
    header = resp.headers.get("retry-after", "").strip()
    if header.isdigit():
        return float(header)
    if isinstance(body, dict):
        details = (body.get("error") or {}).get("details") or []
        for d in details:
            if isinstance(d, dict) and str(d.get("@type", "")).endswith("RetryInfo"):
                m = _DELAY.match(str(d.get("retryDelay", "")))
                if m:
                    return float(m.group(1))
    return None


class GeminiClient:
    """Google AI Studio Gemini REST(generateContent)."""

    def __init__(self, http: httpx.Client, api_key: str, model: str, base_url: str, timeout: float) -> None:
        self._http = http
        self._key = api_key
        self._model = model
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def generate(self, system: str, messages: Sequence[Message]) -> str:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [
                {"role": "user" if m.role == "user" else "model", "parts": [{"text": m.content}]} for m in messages
            ],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 1024,
                # flash 모델은 기본으로 '생각' 토큰을 써서 답이 늦고 출력 한도를 먹는다. 안내 답변에는 필요 없다
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        url = f"{self._base}/models/{self._model}:generateContent"
        try:
            resp = self._http.post(url, json=body, headers={"x-goog-api-key": self._key}, timeout=self._timeout)
        except httpx.HTTPError as e:
            raise ProviderError(f"gemini 연결 실패: {type(e).__name__}") from e
        try:
            data: object = resp.json()
        except ValueError:
            data = None
        status = ""
        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            status = str(data["error"].get("status", ""))
        if resp.status_code == 429 or status == "RESOURCE_EXHAUSTED":
            raise QuotaExceeded(f"gemini 한도 소진({resp.status_code})", _retry_after(resp, data))
        if resp.status_code >= 400:
            raise ProviderError(f"gemini HTTP {resp.status_code} {status}".strip())
        text = _gemini_text(data)
        if not text:
            raise ProviderError("gemini 빈 답")
        return text


def _gemini_text(data: object) -> str:
    if not isinstance(data, dict):
        return ""
    for cand in data.get("candidates") or []:
        parts = ((cand or {}).get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
        if text.strip():
            return text.strip()
    return ""


class OllamaClient:
    """Ollama /api/chat (stream 끔)."""

    def __init__(self, http: httpx.Client, base_url: str, model: str, timeout: float) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    def generate(self, system: str, messages: Sequence[Message]) -> str:
        body = {
            "model": self._model,
            "stream": False,
            "messages": [{"role": "system", "content": system}]
            + [{"role": m.role, "content": m.content} for m in messages],
            "options": {"temperature": 0.3, "num_predict": 800},
        }
        try:
            resp = self._http.post(f"{self._base}/api/chat", json=body, timeout=self._timeout)
        except httpx.HTTPError as e:
            raise ProviderError(f"ollama 연결 실패: {type(e).__name__}") from e
        if resp.status_code == 404:
            raise ProviderError(f"ollama 모델 없음: {self._model} (ollama pull {self._model})")
        if resp.status_code >= 400:
            raise ProviderError(f"ollama HTTP {resp.status_code}")
        try:
            text = str(resp.json()["message"]["content"]).strip()
        except (ValueError, KeyError, TypeError) as e:
            raise ProviderError("ollama 응답 형식 오류") from e
        if not text:
            raise ProviderError("ollama 빈 답")
        return text


class ChatRouter:
    """Gemini → Ollama 순서로 묻는다. Gemini가 한도를 알리면 쿨다운 동안 바로 Ollama로 간다."""

    def __init__(
        self,
        gemini: Generator | None,
        ollama: Generator,
        cooldown_seconds: float,
        error_cooldown_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._gemini = gemini
        self._ollama = ollama
        self._cooldown = cooldown_seconds
        self._error_cooldown = error_cooldown_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._gemini_blocked_until = 0.0

    def gemini_available(self) -> bool:
        with self._lock:
            return self._gemini is not None and self._clock() >= self._gemini_blocked_until

    def _block_gemini(self, seconds: float) -> None:
        with self._lock:
            self._gemini_blocked_until = max(self._gemini_blocked_until, self._clock() + seconds)

    def reply(self, system: str, messages: Sequence[Message]) -> Reply:
        if self._gemini is not None and self.gemini_available():
            try:
                return Reply(self._gemini.generate(system, messages), "gemini")
            except QuotaExceeded as e:
                wait = max(self._cooldown, e.retry_after or 0.0)
                log.warning("Gemini 한도 소진, %.0f초 동안 Ollama로 답한다", wait)
                self._block_gemini(wait)
            except ProviderError as e:
                log.warning("Gemini 실패(%s), %.0f초 동안 Ollama로 답한다", e, self._error_cooldown)
                self._block_gemini(self._error_cooldown)
        try:
            return Reply(self._ollama.generate(system, messages), "ollama")
        except ProviderError as e:
            log.error("Ollama도 실패: %s", e)
            raise AllProvidersFailed(str(e)) from e
