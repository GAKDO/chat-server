from trace_chat.config import Settings


def test_환경_변수가_없으면_기본값을_쓰고_키는_없음():
    s = Settings.from_env({})
    assert s.gemini_api_key is None
    assert s.gemini_model == "gemini-2.5-flash"
    assert s.ollama_model == "qwen2.5:3b"
    assert s.ollama_base_url == "http://ollama:11434"


def test_환경_변수로_바꾼다():
    s = Settings.from_env({
        "GEMINI_API_KEY": " k ", "OLLAMA_BASE_URL": "http://host:11434/", "OLLAMA_MODEL": "qwen2.5:7b",
        "GEMINI_COOLDOWN_SECONDS": "60", "CHAT_RATE_LIMIT_PER_MINUTE": "5",
    })
    assert s.gemini_api_key == "k"
    assert s.ollama_base_url == "http://host:11434"
    assert s.ollama_model == "qwen2.5:7b"
    assert s.gemini_cooldown_seconds == 60
    assert s.rate_limit_per_minute == 5
