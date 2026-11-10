"""내 렌탈·검사 결과 읽기."""

import httpx

from trace_chat.context import TraceApi, UserContextCache, build_user_context, wants_account_data

RENTALS = {
    "items": [
        {
            "rentalId": "R1", "serial": "C02EXAMPLE01", "modelCode": "MBA13-M2", "status": "OPEN",
            "createdAt": "2026-11-12T08:50:00+09:00", "startedAt": "2026-11-12T09:00:00+09:00",
            "currentCheckpoint": {"checkpointId": "CP2", "type": "RCV", "status": "OPEN"},
        }
    ],
    "nextCursor": None,
}
RENTAL = {
    "rentalId": "R1",
    "checkpoints": [
        {"checkpointId": "CP1", "type": "OUT", "status": "SEALED", "occurredAt": "2026-11-12T09:10:00+09:00"},
        {"checkpointId": "CP2", "type": "RCV", "status": "OPEN", "occurredAt": None},
    ],
}
INSPECTION = {
    "checkpointId": "CP1", "grade": "B", "reasons": ["긁힘 1개(12mm)"],
    "defects": [{"class": "scratch", "match": {"status": "NEW"}}],
    "review": {"pending": 0, "completedAt": "2026-11-12T09:20:00+09:00"},
}


def api_with(routes: dict[str, httpx.Response], seen: list | None = None) -> TraceApi:
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append((req.method, req.url.path, req.headers.get("authorization")))
        return routes.get(req.url.path, httpx.Response(404))

    return TraceApi(httpx.Client(transport=httpx.MockTransport(handler)), "http://gateway:8080", 5)


def test_렌탈_검사_관련_질문만_내_정보를_읽는다():
    assert wants_account_data("내 렌탈 상태 알려줘")
    assert wants_account_data("검사 결과 어땠어?")
    assert not wants_account_data("안녕하세요")
    assert not wants_account_data("회원가입은 어떻게 해요?")


def test_사용자_토큰으로_GET만_해서_렌탈과_시점별_등급을_정리한다():
    seen: list = []
    api = api_with(
        {
            "/api/v1/rentals": httpx.Response(200, json=RENTALS),
            "/api/v1/rentals/R1": httpx.Response(200, json=RENTAL),
            "/api/v1/captures/checkpoints/CP1/inspection": httpx.Response(200, json=INSPECTION),
        },
        seen,
    )
    text = build_user_context(api, "tok")
    assert "렌탈 R1" in text and "MBA13-M2" in text
    assert "시점 OUT: status SEALED" in text
    assert "등급 B" in text and "scratch 1개" in text and "NEW 1개" in text
    assert "/my/rentals/R1" in text
    assert all(m == "GET" and a == "Bearer tok" for m, _, a in seen)
    # 촬영 전(OPEN) 시점의 검사 결과는 부르지 않는다
    assert ("GET", "/api/v1/captures/checkpoints/CP2/inspection", "Bearer tok") not in seen


def test_토큰이_만료되면_다시_로그인하라고_안내한다():
    api = api_with({"/api/v1/rentals": httpx.Response(401)})
    assert "로그인이 만료" in build_user_context(api, "tok")


def test_렌탈이_없으면_QR_연결을_안내한다():
    api = api_with({"/api/v1/rentals": httpx.Response(200, json={"items": [], "nextCursor": None})})
    assert "연결된 렌탈이 없다" in build_user_context(api, "tok")


def test_서버_오류면_불러오지_못했다고_안내한다():
    api = api_with({"/api/v1/rentals": httpx.Response(500)})
    assert "불러오지 못했다" in build_user_context(api, "tok")


def test_같은_토큰은_캐시에서_꺼내고_시간이_지나면_다시_읽는다():
    t = [0.0]
    cache = UserContextCache(30, clock=lambda: t[0])
    calls = []

    def build() -> str:
        calls.append(1)
        return f"v{len(calls)}"

    assert cache.get_or_build("tok", build) == "v1"
    assert cache.get_or_build("tok", build) == "v1"
    t[0] = 31
    assert cache.get_or_build("tok", build) == "v2"
    assert cache.get_or_build("other", build) == "v3"
