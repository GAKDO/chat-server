"""HTTP API. gateway가 /api/v1/chat/**를 이 서버로 넘긴다.

- GET  /api/v1/chat/ping : 배포 확인(로그인 없이)
- POST /api/v1/chat      : 질문 → 답. 로그인하지 않아도 사이트 사용법은 답하고,
                           Authorization이 있으면 그 사용자의 렌탈·검사 결과도 읽어 답한다.
대화 기록은 서버에 저장하지 않는다. 브라우저가 최근 대화를 함께 보낸다.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .clientip import client_ip, parse_networks
from .config import Settings
from .context import TraceApi, UserContextCache, build_user_context, wants_account_data
from .knowledge import system_prompt
from .llm import AllProvidersFailed, ChatRouter, GeminiClient, Message, OllamaClient
from .ratelimit import DailyLimiter, RateLimiter

log = logging.getLogger(__name__)

# 모델에게 보내는 지난 대화 수(질문·답 합쳐). 길수록 느리고 토큰을 많이 쓴다
MAX_HISTORY = 10


class ChatMessageIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    messages: list[ChatMessageIn] = Field(min_length=1, max_length=40)


class ChatResponse(BaseModel):
    reply: str
    provider: Literal["gemini", "ollama"]


class PingResponse(BaseModel):
    service: str
    version: str
    commit: str


def _problem(status: int, code: str, title: str, detail: str, path: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={
            "type": "about:blank", "title": title, "status": status, "detail": detail, "instance": path, "code": code,
        },
    )


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer " and auth[7:].strip():
        return auth[7:].strip()
    return None


def create_app(
    settings: Settings | None = None,
    router: ChatRouter | None = None,
    api: TraceApi | None = None,
    limiter: RateLimiter | None = None,
    daily: DailyLimiter | None = None,
) -> FastAPI:
    s = settings or Settings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    http = httpx.Client(follow_redirects=False)
    if router is None:
        gemini = (
            GeminiClient(http, s.gemini_api_key, s.gemini_model, s.gemini_base_url, s.gemini_timeout_seconds)
            if s.gemini_api_key
            else None
        )
        if gemini is None:
            log.warning("GEMINI_API_KEY가 없어 Ollama(%s)로만 답한다", s.ollama_model)
        ollama = OllamaClient(http, s.ollama_base_url, s.ollama_model, s.ollama_timeout_seconds)
        router = ChatRouter(gemini, ollama, s.gemini_cooldown_seconds, s.gemini_error_cooldown_seconds)
    api = api or TraceApi(http, s.trace_api_url, s.api_timeout_seconds)
    limiter = limiter or RateLimiter(s.rate_limit_per_minute)
    daily = daily or DailyLimiter(s.daily_limit_per_ip, s.daily_limit_total)
    trusted = parse_networks(s.trusted_proxies)
    cache = UserContextCache()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        http.close()

    app = FastAPI(
        title="TRACE chat-server", version=__version__, docs_url=None, redoc_url=None, openapi_url=None,
        lifespan=lifespan,
    )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem(400, "VALIDATION_FAILED", "요청 형식이 맞지 않습니다", "messages 형식을 확인해 주세요.",
                        request.url.path)

    @app.get("/api/v1/chat/ping", response_model=PingResponse)
    def ping() -> PingResponse:
        return PingResponse(service="chat-server", version=__version__, commit=s.app_commit[:7])

    @app.post("/api/v1/chat", response_model=ChatResponse)
    def chat(body: ChatRequest, request: Request) -> ChatResponse | JSONResponse:
        path = request.url.path
        if body.messages[-1].role != "user":
            detail = "마지막 메시지는 사용자 질문이어야 합니다."
            return _problem(400, "VALIDATION_FAILED", "요청 형식이 맞지 않습니다", detail, path)
        token = _bearer(request)
        # 토큰은 검증하지 않으므로(가짜 토큰으로 제한을 피할 수 있다) 제한은 실제 IP로만 센다
        ip = client_ip(
            request.client.host if request.client else None,
            request.headers.get("x-forwarded-for"),
            request.headers.get("x-real-ip"),
            trusted,
        )
        if not limiter.allow(ip):
            detail = "질문이 너무 잦아요. 1분 뒤 다시 보내 주세요."
            return _problem(429, "RATE_LIMITED", "질문이 너무 많습니다", detail, path)
        over = daily.take(ip)
        if over == "ip":
            detail = "오늘 질문할 수 있는 횟수를 다 썼어요. 내일 다시 물어봐 주세요."
            return _problem(429, "RATE_LIMITED", "오늘 질문 한도를 넘었습니다", detail, path)
        if over == "total":
            log.warning("서비스 전체 하루 질문 상한(%d)에 닿았다", s.daily_limit_total)
            detail = "오늘 도우미 사용량이 가득 찼어요. 내일 다시 물어봐 주세요."
            return _problem(429, "RATE_LIMITED", "오늘 도우미 사용량을 넘었습니다", detail, path)

        question = body.messages[-1].content
        user_context = None
        if token and wants_account_data(question):
            user_context = cache.get_or_build(token, lambda: build_user_context(api, token))
        messages = [Message(m.role, m.content) for m in body.messages[-MAX_HISTORY:]]
        # Gemini는 대화가 사용자 질문으로 시작해야 한다
        while messages and messages[0].role != "user":
            messages.pop(0)
        try:
            reply = router.reply(system_prompt(user_context), messages)
        except AllProvidersFailed:
            return _problem(503, "INTERNAL_ERROR", "챗봇이 잠시 답할 수 없습니다", "잠시 뒤 다시 물어봐 주세요.", path)
        return ChatResponse(reply=reply.text, provider=reply.provider)

    return app
