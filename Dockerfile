# chat-server 컨테이너: 사이트 도우미 챗봇. 학교 서버 Compose에서 돈다(포트 8080, 다른 TRACE 서버와 같음).
# 모델은 이미지에 넣지 않는다. Gemini는 인터넷 API, Ollama는 같은 Compose의 ollama 서비스(또는 OLLAMA_BASE_URL)
FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install . \
    && useradd --system --uid 10001 --home /app trace

ARG APP_COMMIT=dev
ENV APP_COMMIT=${APP_COMMIT}
USER trace
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8080/api/v1/chat/ping', timeout=2)" || exit 1
ENTRYPOINT ["trace-chat"]
