"""사용자(토큰) 또는 IP 하나당 1분 질문 수 제한. chat-server는 한 대만 돌므로 메모리에 둔다."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable


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
