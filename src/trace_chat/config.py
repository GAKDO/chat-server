"""설정은 모두 환경 변수로 받는다. API 키는 코드·로그에 남기지 않는다."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # 비어 있으면 Gemini를 건너뛰고 바로 Ollama로 답한다
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "qwen2.5:3b"
    # 내 렌탈·검사 결과를 읽을 때 부르는 TRACE API 입구(Compose 안 gateway)
    trace_api_url: str = "http://gateway:8080"
    # Gemini가 한도(429·RESOURCE_EXHAUSTED)를 알리면 이 시간 동안 Gemini를 건너뛴다
    gemini_cooldown_seconds: float = 300.0
    # Gemini가 한도가 아닌 이유(5xx·연결 실패)로 실패했을 때 건너뛰는 시간
    gemini_error_cooldown_seconds: float = 30.0
    gemini_timeout_seconds: float = 20.0
    # CPU로 도는 Ollama는 느리므로 넉넉히 둔다
    ollama_timeout_seconds: float = 120.0
    api_timeout_seconds: float = 5.0
    # 사용자(토큰) 또는 IP 하나당 1분에 받을 질문 수
    rate_limit_per_minute: int = 20
    app_commit: str = "dev"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        d = cls()

        def s(name: str, default: str) -> str:
            v = e.get(name, "").strip()
            return v or default

        def f(name: str, default: float) -> float:
            v = e.get(name, "").strip()
            return float(v) if v else default

        return cls(
            gemini_api_key=e.get("GEMINI_API_KEY", "").strip() or None,
            gemini_model=s("GEMINI_MODEL", d.gemini_model),
            gemini_base_url=s("GEMINI_BASE_URL", d.gemini_base_url).rstrip("/"),
            ollama_base_url=s("OLLAMA_BASE_URL", d.ollama_base_url).rstrip("/"),
            ollama_model=s("OLLAMA_MODEL", d.ollama_model),
            trace_api_url=s("TRACE_API_URL", d.trace_api_url).rstrip("/"),
            gemini_cooldown_seconds=f("GEMINI_COOLDOWN_SECONDS", d.gemini_cooldown_seconds),
            gemini_error_cooldown_seconds=f("GEMINI_ERROR_COOLDOWN_SECONDS", d.gemini_error_cooldown_seconds),
            gemini_timeout_seconds=f("GEMINI_TIMEOUT_SECONDS", d.gemini_timeout_seconds),
            ollama_timeout_seconds=f("OLLAMA_TIMEOUT_SECONDS", d.ollama_timeout_seconds),
            api_timeout_seconds=f("TRACE_API_TIMEOUT_SECONDS", d.api_timeout_seconds),
            rate_limit_per_minute=int(f("CHAT_RATE_LIMIT_PER_MINUTE", d.rate_limit_per_minute)),
            app_commit=s("APP_COMMIT", d.app_commit),
        )
