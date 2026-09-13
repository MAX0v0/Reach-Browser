# -*- coding: utf-8 -*-
"""
统一全局配置 — 读取 .env，提供 Claude Opus 5 的 LLM 实例。

所有子模块（Master Agent、browser-use）统一调用 get_llm() / get_browser_llm()，
不在各模块内部硬编码 API Key 或 Base URL。
"""

import os
import sys
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

MODEL_ID: str = "claude-opus-5"


def _api_key() -> str:
    """从环境变量读取 API Key（每次调用都重新读取，保证 load_dotenv 后可见）。"""
    return os.environ.get("ANTHROPIC_API_KEY", "")


def _base_url() -> str | None:
    """从环境变量读取自定义 Base URL；空字符串视为未设置。"""
    return os.environ.get("CUSTOM_BASE_URL", "").strip() or None


def _validate() -> None:
    if not _api_key():
        raise EnvironmentError(
            "ANTHROPIC_API_KEY 未设置。请在 .env 文件中填写你的 API Key。"
        )


# ── LangChain ChatAnthropic 实例（供 Master Agent 使用）──────────────────────

@lru_cache(maxsize=1)
def get_llm():
    """返回全局共享的 LangChain ChatAnthropic 实例（Claude Opus 5）。"""
    _validate()
    from langchain_anthropic import ChatAnthropic

    kwargs: dict = dict(
        model=MODEL_ID,
        anthropic_api_key=_api_key(),
        max_tokens=8192,
    )
    base_url = _base_url()
    if base_url:
        # langchain-anthropic 0.3+ 支持 anthropic_api_url 覆盖 Base URL
        kwargs["anthropic_api_url"] = base_url

    return ChatAnthropic(**kwargs)


# ── browser-use 原生 ChatAnthropic 实例（供 BrowserAgent 使用）──────────────

@lru_cache(maxsize=1)
def get_browser_llm():
    """返回 browser-use 内置 ChatAnthropic 实例（Claude Opus 5）。"""
    _validate()
    browser_use_path = "D:/Code/browser-use"
    if browser_use_path not in sys.path:
        sys.path.insert(0, browser_use_path)

    from browser_use.llm.anthropic.chat import ChatAnthropic as BUChatAnthropic

    kwargs: dict = dict(
        model=MODEL_ID,
        api_key=_api_key(),
        max_tokens=8192,
    )
    base_url = _base_url()
    if base_url:
        kwargs["base_url"] = base_url

    return BUChatAnthropic(**kwargs)
