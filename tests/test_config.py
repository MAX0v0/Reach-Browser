# -*- coding: utf-8 -*-
"""
Tests for config.py — LLM factory and environment variable handling.

Run with:  pytest tests/test_config.py -v
"""

import os
import sys
import importlib

import pytest

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── helpers ───────────────────────────────────────────────────────────────────

def _reload_config(env: dict):
    """Reload config module with a patched environment."""
    os.environ.update(env)
    import config
    importlib.reload(config)
    return config


# ── _api_key / _base_url ──────────────────────────────────────────────────────

def test_api_key_reads_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
    import config
    importlib.reload(config)
    assert config._api_key() == "test-key-123"


def test_base_url_none_when_empty(monkeypatch):
    monkeypatch.setenv("CUSTOM_BASE_URL", "")
    import config
    importlib.reload(config)
    assert config._base_url() is None


def test_base_url_returns_value_when_set(monkeypatch):
    monkeypatch.setenv("CUSTOM_BASE_URL", "https://proxy.example.com")
    import config
    importlib.reload(config)
    assert config._base_url() == "https://proxy.example.com"


def test_base_url_strips_whitespace(monkeypatch):
    monkeypatch.setenv("CUSTOM_BASE_URL", "  https://proxy.example.com  ")
    import config
    importlib.reload(config)
    assert config._base_url() == "https://proxy.example.com"


# ── _validate ─────────────────────────────────────────────────────────────────

def test_validate_raises_when_no_key(monkeypatch):
    # Patch _api_key directly: reload(config) re-runs load_dotenv(), which
    # would restore the real key from the on-disk .env file.
    import config
    monkeypatch.setattr(config, "_api_key", lambda: "")
    with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
        config._validate()


def test_validate_passes_when_key_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    import config
    importlib.reload(config)
    config._validate()  # should not raise


# ── get_llm ───────────────────────────────────────────────────────────────────

def test_get_llm_raises_without_key(monkeypatch):
    import config
    monkeypatch.setattr(config, "_api_key", lambda: "")
    config.get_llm.cache_clear()
    with pytest.raises(EnvironmentError):
        config.get_llm()
    config.get_llm.cache_clear()


def test_get_llm_returns_chat_anthropic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("CUSTOM_BASE_URL", "")
    import config
    importlib.reload(config)
    config.get_llm.cache_clear()
    llm = config.get_llm()
    # Should be a LangChain ChatAnthropic instance
    assert llm is not None
    assert hasattr(llm, "invoke")


def test_get_llm_is_cached(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    import config
    importlib.reload(config)
    config.get_llm.cache_clear()
    llm1 = config.get_llm()
    llm2 = config.get_llm()
    assert llm1 is llm2


# ── get_browser_llm ───────────────────────────────────────────────────────────

def test_get_browser_llm_raises_without_key(monkeypatch):
    import config
    monkeypatch.setattr(config, "_api_key", lambda: "")
    config.get_browser_llm.cache_clear()
    with pytest.raises(EnvironmentError):
        config.get_browser_llm()
    config.get_browser_llm.cache_clear()


def test_get_browser_llm_returns_instance(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("CUSTOM_BASE_URL", "")
    import config
    importlib.reload(config)
    config.get_browser_llm.cache_clear()
    llm = config.get_browser_llm()
    assert llm is not None
    assert hasattr(llm, "ainvoke")
