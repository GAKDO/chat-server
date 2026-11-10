"""질문한 사람의 실제 IP. 요청 제한은 이 값으로만 센다.

토큰은 chat-server가 검증하지 않으므로(아무 글자나 넣을 수 있다) 제한 기준으로 쓰지 않는다.
학교 서버의 경로는 사용자 → 학교 프록시(nginx) → gateway → chat-server이다.
X-Forwarded-For는 앞쪽 값을 사용자가 꾸밀 수 있으므로, 맨 오른쪽(가장 가까운 쪽)부터 읽으며
믿는 프록시(학교 프록시·도커 내부 주소)를 건너뛰고 처음 나오는 주소를 사용자 IP로 본다.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence

IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# 학교 프록시와 도커·사설·루프백 대역
DEFAULT_TRUSTED_PROXIES = "220.67.216.12,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8,::1/128,fc00::/7"


def parse_networks(text: str) -> tuple[IpNetwork, ...]:
    """쉼표로 나눈 IP·CIDR 목록. 읽지 못한 항목은 건너뛴다."""
    nets: list[IpNetwork] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return tuple(nets)


def _ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    v = value.strip().strip('"')
    # [IPv6]:port, IPv4:port 꼴도 받는다
    if v.startswith("["):
        v = v[1:].split("]", 1)[0]
    elif v.count(":") == 1:
        v = v.split(":", 1)[0]
    try:
        return ipaddress.ip_address(v)
    except ValueError:
        return None


def client_ip(peer: str | None, forwarded_for: str | None, real_ip: str | None,
              trusted: Sequence[IpNetwork]) -> str:
    """peer는 chat-server에 직접 붙은 주소(gateway 컨테이너)."""

    def is_trusted(a: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return any(a.version == n.version and a in n for n in trusted)

    chain = [a for a in (_ip(x) for x in (forwarded_for or "").split(",")) if a is not None]
    peer_ip = _ip(peer) if peer else None
    if peer_ip is not None:
        chain.append(peer_ip)
    # 직접 붙은 쪽이 믿는 프록시가 아니면 헤더는 아무도 보증하지 않으므로 그 주소를 쓴다
    if peer_ip is not None and not is_trusted(peer_ip):
        return str(peer_ip)
    for a in reversed(chain):
        if not is_trusted(a):
            return str(a)
    # 모두 믿는 프록시라면(앞단이 X-Forwarded-For를 넘기지 않은 경우) 프록시가 넣은 X-Real-IP를 쓴다
    real = _ip(real_ip) if real_ip else None
    if real is not None:
        return str(real)
    if chain:
        return str(chain[0])
    return "unknown"
