"""요청 제한. chat-server는 한 대만 돌므로 메모리에 둔다(다시 시작하면 처음부터 센다).

- IP 하나당 1분 질문 수
- IP 하나당 하루 질문 수, 서비스 전체 하루 질문 수(Gemini 사용량 상한). 하루는 한국 시간 자정에 바뀐다
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


class RateLimiter:
    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit = per_minute
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        if self._limit <= 0:
            return True
        now = self._clock()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and q[0] <= now - 60:
                q.popleft()
            if len(q) >= self._limit:
                return False
            q.append(now)
            if len(self._hits) > 10_000:
                self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > now - 60}
            return True


class DailyLimiter:
    """하루 상한. 0 이하면 그 상한은 끈다. 넘으면 'ip' 또는 'total'을 돌려준다."""

    def __init__(self, per_ip: int, total: int, now: Callable[[], datetime] = lambda: datetime.now(KST)) -> None:
        self._per_ip = per_ip
        self._total = total
        self._now = now
        self._lock = threading.Lock()
        self._day = ""
        self._count_total = 0
        self._count_ip: dict[str, int] = {}

    def take(self, ip: str) -> str | None:
        day = self._now().astimezone(KST).date().isoformat()
        with self._lock:
            if day != self._day:
                self._day, self._count_total, self._count_ip = day, 0, {}
            if self._total > 0 and self._count_total >= self._total:
                return "total"
            used = self._count_ip.get(ip, 0)
            if self._per_ip > 0 and used >= self._per_ip:
                return "ip"
            self._count_total += 1
            self._count_ip[ip] = used + 1
            return None
