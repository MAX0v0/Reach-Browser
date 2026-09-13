# -*- coding: utf-8 -*-
"""
Tests for core/backend_resolver.py — 复用 Agent-Reach channel 体检决定链路顺序。

Agent-Reach 的体检调用全部 mock，离线可跑。

Run with:  pytest tests/test_backend_resolver.py -v
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import backend_resolver as br
from core.routes import Route, get_routes


# ── _tier_of：后端标签归一化 ──────────────────────────────────────────────────

@pytest.mark.parametrize("backend,expected", [
    ("bili-cli", "CLI"),
    ("gh CLI", "CLI"),
    ("yt-dlp", "CLI"),
    ("twitter-cli", "CLI"),
    ("OpenCLI", "MCP"),
    ("xiaohongshu-mcp", "MCP"),
    ("B站搜索 API", "API"),
    ("内置", "API"),
])
def test_tier_of_known_backends(backend, expected):
    assert br._tier_of(backend) == expected


def test_tier_of_none_is_none():
    assert br._tier_of(None) is None
    assert br._tier_of("") is None


def test_tier_of_unknown_backend_is_none():
    assert br._tier_of("完全没见过的后端") is None


def test_tier_of_matches_by_prefix():
    """上游可能给后端标签带上版本或补充说明。"""
    assert br._tier_of("bili-cli v0.9") == "CLI"


def test_opencli_variant_maps_to_mcp_not_cli():
    """OpenCLI 名字里含 cli，但它是浏览器会话后端，必须归 MCP。"""
    assert br._tier_of("opencli-bridge") == "MCP"


# ── resolve_active_tier ───────────────────────────────────────────────────────

def _fake_channel(backend, status="ok", msg="fine"):
    ch = MagicMock()
    ch.check.return_value = (status, msg)
    ch.active_backend = backend
    return ch


def test_resolve_returns_tier_of_active_backend():
    with patch("agent_reach.channels.get_channel",
               return_value=_fake_channel("B站搜索 API")):
        tier, msg = br.resolve_active_tier("bilibili")
    assert tier == "API"
    assert "B站搜索 API" in msg


def test_resolve_returns_none_when_no_active_backend():
    """体检 warn 且没有可用后端时，不该瞎猜某一级。"""
    with patch("agent_reach.channels.get_channel",
               return_value=_fake_channel(None, "warn", "gh CLI 未安装")):
        tier, msg = br.resolve_active_tier("github")
    assert tier is None
    assert "未给出可用后端" in msg


def test_resolve_handles_unknown_platform():
    with patch("agent_reach.channels.get_channel", return_value=None):
        tier, msg = br.resolve_active_tier("nosuchplatform")
    assert tier is None
    assert "无 nosuchplatform channel" in msg


def test_resolve_survives_channel_exception():
    """体检自身抛异常不能让主流程崩掉。"""
    ch = MagicMock()
    ch.check.side_effect = RuntimeError("probe exploded")
    with patch("agent_reach.channels.get_channel", return_value=ch):
        tier, msg = br.resolve_active_tier("bilibili")
    assert tier is None
    assert "体检异常" in msg


# ── order_routes ──────────────────────────────────────────────────────────────

_CHAIN = [
    Route("cli-tool", "CLI", "cli cmd"),
    Route("opencli", "MCP", "mcp cmd"),
    Route("api", "API", "api cmd"),
]


def test_order_promotes_active_tier_to_front():
    with patch.object(br, "resolve_active_tier", return_value=("API", "probe")):
        ordered, _ = br.order_routes(_CHAIN, "bilibili")
    assert [r.tier for r in ordered] == ["API", "CLI", "MCP"]


def test_order_never_drops_any_tier():
    """体检只是建议，不能裁掉任何一级——实际可用性由执行判定。"""
    with patch.object(br, "resolve_active_tier", return_value=("API", "probe")):
        ordered, _ = br.order_routes(_CHAIN, "bilibili")
    assert sorted(r.tier for r in ordered) == sorted(r.tier for r in _CHAIN)
    assert len(ordered) == len(_CHAIN)


def test_order_keeps_default_when_probe_inconclusive():
    with patch.object(br, "resolve_active_tier", return_value=(None, "unknown")):
        ordered, _ = br.order_routes(_CHAIN, "bilibili")
    assert ordered == _CHAIN


def test_order_keeps_default_when_tier_absent_from_chain():
    """体检说 MCP 活跃，但本项目该平台没有 MCP 级路由。"""
    chain = [Route("cli", "CLI", "c"), Route("api", "API", "a")]
    with patch.object(br, "resolve_active_tier", return_value=("MCP", "probe")):
        ordered, msg = br.order_routes(chain, "github")
    assert ordered == chain
    assert "无该级路由" in msg


def test_order_preserves_relative_order_within_tier():
    chain = [
        Route("api-a", "API", "a"),
        Route("api-b", "API", "b"),
        Route("cli", "CLI", "c"),
    ]
    with patch.object(br, "resolve_active_tier", return_value=("API", "p")):
        ordered, _ = br.order_routes(chain, "x")
    assert [r.label for r in ordered] == ["api-a", "api-b", "cli"]


def test_order_works_on_real_bilibili_chain():
    with patch.object(br, "resolve_active_tier", return_value=("API", "probe")):
        ordered, _ = br.order_routes(get_routes("bilibili", "rank"), "bilibili")
    assert ordered[0].tier == "API"
    assert len(ordered) == 3
