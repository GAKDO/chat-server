# 이 파일이 확인하는 것: 로그인한 사용자의 렌탈·검사 기록을 읽어 AI에게 줄 [내 정보] 글로 정리하는 부분.
# - 렌탈 목록 → 렌탈 상세(출고 OUT·수령 RCV·반납 RET 시점) → 봉인된 시점의 검사 결과(등급·흠집) 순서로 읽는다.
# - 챗봇은 읽기만 한다: 사용자 토큰으로 GET만 부르고 아무것도 바꾸지 않는다.
# - 외부 AI로 보내므로 일련번호는 뒤 4자리만 남긴다.
# TRACE 서버는 부르지 않고 httpx MockTransport로 정해진 답을 돌려준다.

"""내 렌탈·검사 결과 읽기."""

import httpx

from trace_chat.context import (
    TraceApi,
    UserContextCache,
    build_user_context,
    has_account_data,
    mask_serial,
    wants_account_data,
)

# 가짜 렌탈 목록: 맥북 에어 13 M2를 빌린 렌탈 R1 하나, 지금 수령(RCV) 시점이 열려 있는 상태.
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
# 가짜 렌탈 상세: 출고(OUT) 시점 CP1은 촬영·검사가 끝나 봉인(SEALED)됐고, 수령(RCV) 시점 CP2는 아직 열림(OPEN).
RENTAL = {
    "rentalId": "R1",
    "checkpoints": [
        {"checkpointId": "CP1", "type": "OUT", "status": "SEALED", "occurredAt": "2026-11-12T09:10:00+09:00"},
        {"checkpointId": "CP2", "type": "RCV", "status": "OPEN", "occurredAt": None},
    ],
}
# 가짜 검사 결과(출고 시점 CP1): 등급 B, 새로 생긴(NEW) 긁힘 1개, 관리자 검수 완료.
INSPECTION = {
    "checkpointId": "CP1", "grade": "B", "reasons": ["긁힘 1개(12mm)"],
    "defects": [{"class": "scratch", "match": {"status": "NEW"}}],
    "review": {"pending": 0, "completedAt": "2026-11-12T09:20:00+09:00"},
}


# 주소별로 정해 둔 답을 돌려주는 가짜 TRACE 서버를 만든다. seen에 (방식, 주소, 토큰)을 기록한다.
def api_with(routes: dict[str, httpx.Response], seen: list | None = None) -> TraceApi:
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append((req.method, req.url.path, req.headers.get("authorization")))
        return routes.get(req.url.path, httpx.Response(404))

    return TraceApi(httpx.Client(transport=httpx.MockTransport(handler)), "http://gateway:8080", 5)


# [상황] 여러 질문 문장을 넣어 본다.
# [기대] "렌탈", "검사 결과"가 들어간 질문만 개인 기록이 필요하다고 판단한다. 인사·회원가입 질문은 아니다.
def test_렌탈_검사_관련_질문만_내_정보를_읽는다():
    assert wants_account_data("내 렌탈 상태 알려줘")
    assert wants_account_data("검사 결과 어땠어?")
    assert not wants_account_data("안녕하세요")
    assert not wants_account_data("회원가입은 어떻게 해요?")


# [상황] 로그인한 사용자의 기록을 정리한다(렌탈 R1, 출고 봉인, 수령 열림).
# [기대] 글에 렌탈 번호·기종·출고 시점 상태·등급 B·긁힘 1개(새로 생김)·내 렌탈 화면 주소가 들어간다.
#        모든 요청은 사용자 토큰을 붙인 GET뿐이고, 아직 촬영 전인 수령 시점(CP2)의 검사 결과는 부르지 않는다.
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


# [상황] TRACE 서버가 401(로그인 만료)로 답한다.
# [기대] AI가 "로그인이 만료됐으니 다시 로그인하라"고 안내할 수 있는 글을 만든다.
#        ※ 응답 코드: 401 = 로그인 필요(토큰이 없거나 만료·위조됨)
def test_토큰이_만료되면_다시_로그인하라고_안내한다():
    api = api_with({"/api/v1/rentals": httpx.Response(401)})
    assert "로그인이 만료" in build_user_context(api, "tok")


# [상황] 이 사용자에게 연결된 렌탈이 하나도 없다.
# [기대] "연결된 렌탈이 없다"는 글을 만들어 QR로 렌탈을 연결하라고 안내하게 한다.
def test_렌탈이_없으면_QR_연결을_안내한다():
    api = api_with({"/api/v1/rentals": httpx.Response(200, json={"items": [], "nextCursor": None})})
    assert "연결된 렌탈이 없다" in build_user_context(api, "tok")


# [상황] TRACE 서버가 500(서버 오류)으로 답한다.
# [기대] "불러오지 못했다"는 글을 만든다(챗봇 자체는 멈추지 않음).
#        ※ 응답 코드: 500 = 서버 내부 오류
def test_서버_오류면_불러오지_못했다고_안내한다():
    api = api_with({"/api/v1/rentals": httpx.Response(500)})
    assert "불러오지 못했다" in build_user_context(api, "tok")


# [상황] 같은 사용자가 짧은 시간에 여러 번 묻는다(캐시 유지 30초).
# [기대] 30초 안에는 저장해 둔 글을 다시 쓰고(v1), 31초가 지나면 새로 읽는다(v2).
#        다른 사용자(other)는 자기 것을 따로 읽는다(v3).
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


# [상황] 기기 일련번호가 C02EXAMPLE01인 렌탈의 기록을 정리한다.
# [기대] 글에 일련번호 전체는 없고 "뒤 4자리 LE01"만 있다. 실제 기록이 들어간 글로 판정된다.
def test_외부_모델로_보내는_내_정보에는_일련번호_전체가_없고_뒤_4자리만_있다():
    api = api_with(
        {
            "/api/v1/rentals": httpx.Response(200, json=RENTALS),
            "/api/v1/rentals/R1": httpx.Response(200, json=RENTAL),
            "/api/v1/captures/checkpoints/CP1/inspection": httpx.Response(200, json=INSPECTION),
        }
    )
    text = build_user_context(api, "tok")
    assert "C02EXAMPLE01" not in text
    assert "뒤 4자리 LE01" in text
    assert has_account_data(text)


# [상황] 일련번호가 4자리 이하이거나, 없거나, 공백뿐이다.
# [기대] 짧으면 전부 별표로 가리고, 없거나 빈 값은 "?"로 쓴다(일부라도 노출하지 않음).
def test_일련번호가_짧거나_없으면_전부_가린다():
    assert mask_serial("AB12") == "****"
    assert mask_serial(None) == "?"
    assert mask_serial("  ") == "?"


# [상황] 로그인 만료나 렌탈 없음 같은 안내문만 만들어졌다.
# [기대] 실제 기록을 읽은 게 아니므로 답 끝의 "TRACE 기록 조회 기준" 꼬리말 대상이 아니다.
def test_기록을_못_읽은_안내문은_근거_꼬리말_대상이_아니다():
    assert not has_account_data(None)
    assert not has_account_data(build_user_context(api_with({"/api/v1/rentals": httpx.Response(401)}), "tok"))
    empty = api_with({"/api/v1/rentals": httpx.Response(200, json={"items": [], "nextCursor": None})})
    assert not has_account_data(build_user_context(empty, "tok"))
