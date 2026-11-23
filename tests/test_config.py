# 이 파일이 확인하는 것: 챗봇 서버 설정을 환경 변수에서 읽는 규칙.
# 서버를 띄울 때 환경 변수를 안 주면 어떤 기본값을 쓰는지, 주면 제대로 바뀌는지 본다.
# (테스트에 쓰는 키 값은 진짜 키가 아닌 임의 글자다.)

from trace_chat.config import Settings


# [상황] 환경 변수를 하나도 주지 않고 설정을 읽는다.
# [기대] Gemini 키는 없음(→ 처음부터 Ollama로 답함), 모델은 gemini-3.5-flash·qwen2.5:3b,
#        Ollama 주소는 같은 Compose 안의 ollama, 하루 상한은 IP당 100번·전체 1000번,
#        믿는 프록시 목록에 학교 프록시 주소가 들어 있다.
def test_환경_변수가_없으면_기본값을_쓰고_키는_없음():
    s = Settings.from_env({})
    assert s.gemini_api_key is None
    assert s.gemini_model == "gemini-3.5-flash"
    assert s.ollama_model == "qwen2.5:3b"
    assert s.ollama_base_url == "http://ollama:11434"
    assert (s.daily_limit_per_ip, s.daily_limit_total) == (100, 1000)
    assert "220.67.216.12" in s.trusted_proxies


# [상황] 키·주소·모델·쿨다운·요청 제한 값을 환경 변수로 준다(앞뒤 공백, 주소 끝 / 포함).
# [기대] 공백과 끝 /는 정리하고 준 값대로 설정이 바뀐다. 전체 상한 0은 "상한 없음"으로 그대로 둔다.
def test_환경_변수로_바꾼다():
    s = Settings.from_env({
        "GEMINI_API_KEY": " k ", "OLLAMA_BASE_URL": "http://host:11434/", "OLLAMA_MODEL": "qwen2.5:7b",
        "GEMINI_COOLDOWN_SECONDS": "60", "CHAT_RATE_LIMIT_PER_MINUTE": "5",
        "CHAT_DAILY_LIMIT_PER_IP": "30", "CHAT_DAILY_LIMIT_TOTAL": "0", "CHAT_TRUSTED_PROXIES": "10.0.0.1",
    })
    assert s.gemini_api_key == "k"
    assert s.ollama_base_url == "http://host:11434"
    assert s.ollama_model == "qwen2.5:7b"
    assert s.gemini_cooldown_seconds == 60
    assert s.rate_limit_per_minute == 5
    assert (s.daily_limit_per_ip, s.daily_limit_total, s.trusted_proxies) == (30, 0, "10.0.0.1")
