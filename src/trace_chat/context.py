"""로그인한 사용자의 렌탈·검사 결과를 읽어 모델에게 줄 [내 정보]를 만든다.

사용자 본인의 토큰을 그대로 gateway에 보내 읽으므로, 권한 검사는 기존 서버가 한다(CUSTOMER는 내 렌탈만).
읽기(GET)만 한다. 질문에 렌탈·검사 관련 말이 있을 때만 부르고, 같은 토큰은 잠시 캐시해 서버 호출을 줄인다.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

# 이 말이 질문에 있으면 내 렌탈·검사 결과를 읽어 온다
ACCOUNT_KEYWORDS = (
    "렌탈", "대여", "빌린", "빌려", "검사", "흠집", "긁힘", "찍힘", "균열", "등급", "결과", "상태",
    "반납", "수령", "출고", "봉인", "진단", "배터리", "내 노트북", "제 노트북", "기기", "리포트",
)

MAX_RENTALS = 5
MAX_DETAILED_RENTALS = 3


def wants_account_data(text: str) -> bool:
    return any(k in text for k in ACCOUNT_KEYWORDS)


class UnauthenticatedError(Exception):
    """토큰이 없거나 만료됨(401)."""


@dataclass
class TraceApi:
    http: httpx.Client
    base_url: str
    timeout: float

    def _get(self, token: str, path: str, params: dict[str, Any] | None = None) -> Any | None:
        resp = self.http.get(
            f"{self.base_url}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=self.timeout,
        )
        if resp.status_code == 401:
            raise UnauthenticatedError
        if resp.status_code in (403, 404):
            return None
        resp.raise_for_status()
        return resp.json()

    def my_rentals(self, token: str) -> list[dict[str, Any]]:
        page = self._get(token, "/api/v1/rentals", {"limit": MAX_RENTALS})
        items = (page or {}).get("items") or []
        return [i for i in items if isinstance(i, dict)]

    def rental(self, token: str, rental_id: str) -> dict[str, Any] | None:
        return self._get(token, f"/api/v1/rentals/{rental_id}")

    def inspection(self, token: str, checkpoint_id: str) -> dict[str, Any] | None:
        return self._get(token, f"/api/v1/captures/checkpoints/{checkpoint_id}/inspection")


def _inspection_line(insp: dict[str, Any]) -> str:
    defects = [d for d in insp.get("defects") or [] if isinstance(d, dict)]
    by_class = Counter(str(d.get("class")) for d in defects)
    new = sum(1 for d in defects if (d.get("match") or {}).get("status") == "NEW")
    parts = [f"등급 {insp.get('grade') or '아직 없음'}"]
    if defects:
        parts.append("흠집 " + ", ".join(f"{k} {v}개" for k, v in sorted(by_class.items())))
        if new:
            parts.append(f"그중 NEW {new}개")
    else:
        parts.append("확정 흠집 없음")
    reasons = [str(r) for r in insp.get("reasons") or []]
    if reasons:
        parts.append("사유: " + "; ".join(reasons[:3]))
    review = insp.get("review") or {}
    if isinstance(review, dict) and not review.get("completedAt") and review.get("pending"):
        parts.append(f"관리자 검수 대기 {review.get('pending')}곳")
    return ", ".join(parts)


def build_user_context(api: TraceApi, token: str) -> str:
    """렌탈 최대 5개와, 그중 최근 3개의 시점별 검사 결과를 짧은 글로 만든다."""
    try:
        rentals = api.my_rentals(token)
    except UnauthenticatedError:
        return "로그인이 만료되었다. 다시 로그인하면 내 렌탈을 확인할 수 있다고 안내한다."
    except httpx.HTTPError as e:
        log.warning("렌탈 목록을 읽지 못함: %s", type(e).__name__)
        return "지금은 내 렌탈 정보를 불러오지 못했다. 잠시 뒤 다시 묻거나 /my/rentals 화면에서 확인하라고 안내한다."
    if not rentals:
        return "이 계정에 연결된 렌탈이 없다. 수령 QR을 찍고 로그인하면 렌탈이 연결된다."

    lines = [f"이 계정으로 보이는 최근 렌탈 {len(rentals)}개(최근 순):"]
    for i, r in enumerate(rentals):
        cur = r.get("currentCheckpoint") or {}
        lines.append(
            f"- 렌탈 {r.get('rentalId')}: 기종 {r.get('modelCode') or '?'}, 일련번호 {r.get('serial') or '?'}, "
            f"렌탈 status {r.get('status')}, 시작 {r.get('startedAt') or r.get('createdAt') or '?'}, "
            f"지금 시점 {cur.get('type') or '-'}({cur.get('status') or '-'}), 리포트 /my/rentals/{r.get('rentalId')}"
        )
        if i >= MAX_DETAILED_RENTALS:
            continue
        try:
            detail = api.rental(token, str(r.get("rentalId")))
            for cp in (detail or {}).get("checkpoints") or []:
                if not isinstance(cp, dict):
                    continue
                line = f"  · 시점 {cp.get('type')}: status {cp.get('status')}, 촬영 {cp.get('occurredAt') or '아직'}"
                if cp.get("status") in ("GRADED", "SEALED", "CAPTURED") and cp.get("checkpointId"):
                    insp = api.inspection(token, str(cp["checkpointId"]))
                    if insp:
                        line += ", " + _inspection_line(insp)
                lines.append(line)
        except UnauthenticatedError:
            break
        except httpx.HTTPError as e:
            log.warning("렌탈 상세를 읽지 못함: %s", type(e).__name__)
            lines.append("  · 시점별 결과를 지금 불러오지 못했다")
    return "\n".join(lines)


class UserContextCache:
    """같은 토큰의 [내 정보]를 잠시 둔다. 토큰 원문이 아니라 해시를 키로 쓴다."""

    def __init__(self, ttl_seconds: float = 30.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._items: dict[str, tuple[float, str]] = {}

    def get_or_build(self, token: str, build: Callable[[], str]) -> str:
        key = hashlib.sha256(token.encode()).hexdigest()
        now = self._clock()
        with self._lock:
            hit = self._items.get(key)
            if hit and hit[0] > now:
                return hit[1]
        value = build()
        with self._lock:
            # 오래된 항목은 넣을 때 같이 지운다(메모리가 계속 늘지 않게)
            self._items = {k: v for k, v in self._items.items() if v[0] > now}
            self._items[key] = (now + self._ttl, value)
        return value
