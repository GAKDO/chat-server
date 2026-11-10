"""`trace-chat` 명령: uvicorn으로 8080에서 띄운다(다른 TRACE 서버와 같은 포트)."""

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "trace_chat.app:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - 컨테이너 안에서만 열린다
        port=int(os.environ.get("PORT", "8080")),
        proxy_headers=True,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
