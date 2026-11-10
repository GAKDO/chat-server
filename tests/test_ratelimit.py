"""요청 제한과 실제 IP."""

from datetime import datetime

from trace_chat.clientip import DEFAULT_TRUSTED_PROXIES, client_ip, parse_networks
from trace_chat.ratelimit import KST, DailyLimiter

TRUSTED = parse_networks(DEFAULT_TRUSTED_PROXIES)


def test_gateway_뒤에서는_프록시를_건너뛴_가장_가까운_주소를_쓴다():
    assert client_ip("172.18.0.5", "203.0.113.7, 220.67.216.12", None, TRUSTED) == "203.0.113.7"
    assert client_ip("172.18.0.5", "6.6.6.6, 203.0.113.7, 220.67.216.12", None, TRUSTED) == "203.0.113.7"


def test_X_Forwarded_For가_없으면_X_Real_IP_그다음_연결_주소():
    assert client_ip("172.18.0.5", "220.67.216.12", "203.0.113.7", TRUSTED) == "203.0.113.7"
    assert client_ip("172.18.0.5", None, None, TRUSTED) == "172.18.0.5"


def test_믿지_않는_곳에서_직접_붙으면_헤더를_무시한다():
    assert client_ip("198.51.100.9", "203.0.113.7", "203.0.113.8", TRUSTED) == "198.51.100.9"


def test_IP_모양이_아닌_값과_포트는_정리한다():
    assert client_ip("172.18.0.5", "not-an-ip, 203.0.113.7:5555, 220.67.216.12", None, TRUSTED) == "203.0.113.7"
    assert parse_networks("220.67.216.12, nope, 10.0.0.0/8") == TRUSTED[:2]


def test_하루_상한은_한국_시간_자정에_초기화된다():
    now = [datetime(2026, 11, 10, 23, 59, tzinfo=KST)]
    d = DailyLimiter(per_ip=1, total=0, now=lambda: now[0])
    assert d.take("a") is None
    assert d.take("a") == "ip"
    now[0] = datetime(2026, 11, 11, 0, 0, tzinfo=KST)
    assert d.take("a") is None


def test_상한을_넘은_요청은_세지_않는다():
    d = DailyLimiter(per_ip=1, total=2, now=lambda: datetime(2026, 11, 10, tzinfo=KST))
    assert d.take("a") is None
    assert d.take("a") == "ip"
    assert d.take("b") is None
    assert d.take("c") == "total"
