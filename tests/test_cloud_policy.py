import pytest

from runtime import model_router, orchestrator
import autofix_llm_check


def test_local_policy_disables_cloud_even_with_api_keys(monkeypatch):
    monkeypatch.setenv("TENAZAS_ALLOW_CLOUD", "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    assert not orchestrator._is_openrouter_available()
    assert not orchestrator._is_gemini_available()
    assert not autofix_llm_check.check_openrouter()["available"]
    assert not autofix_llm_check.check_gemini()["available"]
    with pytest.raises(RuntimeError, match="disabled"):
        model_router.call_openrouter("test")
