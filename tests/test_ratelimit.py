# 이 파일이 확인하는 것: 요청 제한에 쓰는 "진짜 사용자 IP" 찾기와 하루 상한.
# 사용자 → 학교 프록시(220.67.216.12) → gateway → 챗봇 순서로 요청이 들어오므로,
# 헤더(X-Forwarded-For·X-Real-IP)에서 프록시를 건너뛴 실제 사용자 주소를 골라야 한다.
# 누군가 헤더를 꾸며 다른 사람인 척해도 속지 않는지 본다.

"""요청 제한과 실제 IP."""

from datetime import datetime

from trace_chat.clientip import DEFAULT_TRUSTED_PROXIES, client_ip, parse_networks
from trace_chat.ratelimit import KST, DailyLimiter

# 믿는 프록시 목록(학교 프록시·사설망 대역 등, 기본값).
TRUSTED = parse_networks(DEFAULT_TRUSTED_PROXIES)


# [상황] gateway(172.18.0.5)를 거쳐 들어왔고 헤더에 "사용자, 학교 프록시" 순서로 적혀 있다.
#        두 번째 줄은 맨 앞에 꾸민 주소(6.6.6.6)가 하나 더 붙어 있다.
# [기대] 믿는 프록시를 뒤에서부터 건너뛰고 처음 만나는 주소 203.0.113.7을 쓴다(꾸민 맨 앞 값은 무시).
def test_gateway_뒤에서는_프록시를_건너뛴_가장_가까운_주소를_쓴다():
    assert client_ip("172.18.0.5", "203.0.113.7, 220.67.216.12", None, TRUSTED) == "203.0.113.7"
    assert client_ip("172.18.0.5", "6.6.6.6, 203.0.113.7, 220.67.216.12", None, TRUSTED) == "203.0.113.7"


# [상황] X-Forwarded-For에 프록시 주소뿐이거나 헤더가 아예 없다.
# [기대] X-Real-IP가 있으면 그것을, 그것도 없으면 연결한 주소를 쓴다.
def test_X_Forwarded_For가_없으면_X_Real_IP_그다음_연결_주소():
    assert client_ip("172.18.0.5", "220.67.216.12", "203.0.113.7", TRUSTED) == "203.0.113.7"
    assert client_ip("172.18.0.5", None, None, TRUSTED) == "172.18.0.5"


# [상황] 믿는 프록시가 아닌 곳(198.51.100.9)에서 직접 붙으면서 헤더를 꾸며 보낸다.
# [기대] 헤더를 무시하고 실제 연결 주소를 쓴다.
def test_믿지_않는_곳에서_직접_붙으면_헤더를_무시한다():
    assert client_ip("198.51.100.9", "203.0.113.7", "203.0.113.8", TRUSTED) == "198.51.100.9"


# [상황] 헤더에 IP가 아닌 글자, 포트가 붙은 주소가 섞여 있고, 프록시 목록에도 틀린 값이 섞여 있다.
# [기대] 틀린 값은 버리고 포트는 떼어 203.0.113.7을 고른다. 프록시 목록도 맞는 것만 남긴다.
def test_IP_모양이_아닌_값과_포트는_정리한다():
    assert client_ip("172.18.0.5", "not-an-ip, 203.0.113.7:5555, 220.67.216.12", None, TRUSTED) == "203.0.113.7"
    assert parse_networks("220.67.216.12, nope, 10.0.0.0/8") == TRUSTED[:2]


# [상황] 한 IP 하루 1번 상한에서 밤 11시 59분에 1번 쓰고 또 쓴다.
# [기대] 두 번째는 막히고("ip"), 한국 시간 자정이 지나면 다시 쓸 수 있다.
def test_하루_상한은_한국_시간_자정에_초기화된다():
    now = [datetime(2026, 11, 10, 23, 59, tzinfo=KST)]
    d = DailyLimiter(per_ip=1, total=0, now=lambda: now[0])
    assert d.take("a") is None
    assert d.take("a") == "ip"
    now[0] = datetime(2026, 11, 11, 0, 0, tzinfo=KST)
    assert d.take("a") is None


# [상황] IP당 1번, 전체 2번 상한. a가 2번(1번은 거절), b가 1번, c가 1번 요청한다.
# [기대] 거절된 a의 두 번째는 전체 횟수에 세지 않는다. 그래서 b는 통과하고 c에서 전체 상한("total")에 걸린다.
def test_상한을_넘은_요청은_세지_않는다():
    d = DailyLimiter(per_ip=1, total=2, now=lambda: datetime(2026, 11, 10, tzinfo=KST))
    assert d.take("a") is None
    assert d.take("a") == "ip"
    assert d.take("b") is None
    assert d.take("c") == "total"
