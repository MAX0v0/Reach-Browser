# -*- coding: utf-8 -*-
"""
Tests for core/routes.py — 路由表与三级降级链路。

Run with:  pytest tests/test_routes.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.routes import ROUTES, get_routes, known_platforms


# ── 链路完整性 ────────────────────────────────────────────────────────────────

def test_bilibili_rank_has_three_tiers():
    """B站排行榜必须有 CLI → MCP → API 三级链路。"""
    routes = get_routes("bilibili", "rank")
    tiers = [r.tier for r in routes]
    assert tiers == ["CLI", "MCP", "API"]


def test_bilibili_search_has_three_tiers():
    routes = get_routes("bilibili", "search")
    assert [r.tier for r in routes] == ["CLI", "MCP", "API"]


def test_hot100_maps_to_rank():
    """“Hot100” 应归一化到 rank，而不是 hot。"""
    assert get_routes("bilibili", "hot100") == ROUTES["bilibili"]["rank"]


def test_ranking_alias_maps_to_rank():
    assert get_routes("bilibili", "ranking") == ROUTES["bilibili"]["rank"]


# ── bili-cli 语法正确性（对齐 Agent-Reach video.md）────────────────────────────

def test_bili_commands_have_no_json_flag():
    """bili-cli 不支持 --json；带上会直接报错退出。"""
    for kind, routes in ROUTES["bilibili"].items():
        for r in routes:
            if r.tier == "CLI":
                assert "--json" not in r.command, f"{kind}/{r.label} 误用 --json"


def test_bili_rank_uses_rank_subcommand():
    cli = [r for r in get_routes("bilibili", "rank") if r.tier == "CLI"][0]
    assert cli.command.startswith("bili rank")


def test_bili_search_uses_type_video():
    cli = [r for r in get_routes("bilibili", "search") if r.tier == "CLI"][0]
    assert "--type video" in cli.command


def test_bili_api_delegates_to_api_fetch():
    """API 层不再拼裸 curl（Windows 下 /tmp 与 /dev/null 无效），改走 api_fetch。"""
    api = [r for r in get_routes("bilibili", "rank") if r.tier == "API"][0]
    assert "core.api_fetch" in api.command
    assert "curl" not in api.command
    assert "/tmp/" not in api.command


def test_api_routes_never_use_posix_paths():
    """任何 API 路由都不能出现 POSIX 专有路径，否则 Windows 上必失败。"""
    for platform, kinds in ROUTES.items():
        for kind, routes in kinds.items():
            for r in routes:
                if r.tier == "API":
                    assert "/tmp/" not in r.command, f"{platform}/{kind}"
                    assert "/dev/null" not in r.command, f"{platform}/{kind}"


def test_bili_cookie_warmup_lives_in_api_fetch():
    """Cookie 预热逻辑必须存在于 api_fetch，这是绕过 B站风控的关键。"""
    from core import api_fetch
    assert hasattr(api_fetch, "_bili_warmup")


# ── 占位符 ────────────────────────────────────────────────────────────────────

def test_search_routes_contain_query_placeholder():
    for platform in known_platforms():
        for r in ROUTES[platform].get("search", []):
            assert "{query}" in r.command, f"{platform}/{r.label} 缺少 {{query}}"


# ── 兜底行为 ──────────────────────────────────────────────────────────────────

def test_unknown_platform_returns_empty():
    assert get_routes("unknown", "search") == []
    assert get_routes("web", "rank") == []


def test_unknown_kind_falls_back():
    """未知 route_kind 不应返回空表，要退回可用链路。"""
    assert get_routes("bilibili", "nonsense_kind") != []


def test_v2ex_hot_available():
    routes = get_routes("v2ex", "hot")
    assert len(routes) == 1
    assert routes[0].tier == "API"
