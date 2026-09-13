# -*- coding: utf-8 -*-
"""
Agent-Reach 路由表 — CLI 命令语法严格对齐 Agent-Reach 的 skill 文档。

来源：D:/Code/agent-reach/Agent-Reach/agent_reach/skill/references/*.md

每个平台按 route_kind（rank/hot/search/detail）组织，每个 kind 下是一条
有序的三级降级链路：
    tier 1  CLI   本地 CLI 工具（bili-cli / gh / yt-dlp ...）
    tier 2  MCP   OpenCLI 浏览器会话后端（需桌面 Chrome + 扩展）
    tier 3  API   零依赖 HTTP 直连兜底

Master Agent 按顺序执行，任一层拿到实质数据即停；全部失败才降级 browser-use。

API 层为什么不拼裸 curl：bash_executor 在 Windows 上走 cmd.exe，
`/tmp/xxx` 与 `/dev/null` 无效（实测 curl 退出码 23）；且分页、业务错误码
判断都需要循环逻辑。统一交给 core/api_fetch.py 处理。
"""

from __future__ import annotations

import sys
from typing import NamedTuple


class Route(NamedTuple):
    label: str        # 展示名，用于日志
    tier: str         # CLI / MCP / API
    command: str      # {query} / {n} 占位符由 Master Agent 填充


def _api(platform: str, kind: str, needs_query: bool = False) -> Route:
    """构造调用 core.api_fetch 的 API 层路由。"""
    # 用当前解释器，避免 PATH 里的 python 与运行环境不一致
    exe = sys.executable or "python"
    cmd = f'"{exe}" -m core.api_fetch {platform} {kind} --n {{n}}'
    if needs_query:
        cmd += ' --query "{query}"'
    return Route(f"{platform} {kind} API", "API", cmd)


ROUTES: dict[str, dict[str, list[Route]]] = {
    # ── B站 ───────────────────────────────────────────────────────────────
    # bili-cli 无 --json flag；排行榜用 rank，热门推荐用 hot，语义不同
    "bilibili": {
        "rank": [
            Route("bili-cli rank", "CLI", "bili rank -n {n}"),
            Route("OpenCLI", "MCP", "opencli bilibili rank -f yaml"),
            _api("bilibili", "rank"),
        ],
        "hot": [
            Route("bili-cli hot", "CLI", "bili hot -n {n}"),
            Route("OpenCLI", "MCP", "opencli bilibili rank -f yaml"),
            _api("bilibili", "hot"),
        ],
        "search": [
            Route("bili-cli search", "CLI",
                  'bili search "{query}" --type video -n {n}'),
            Route("OpenCLI", "MCP", 'opencli bilibili search "{query}" -f yaml'),
            _api("bilibili", "search", needs_query=True),
        ],
        "detail": [
            Route("bili-cli video", "CLI", "bili video {query}"),
            Route("OpenCLI", "MCP", "opencli bilibili video {query} -f yaml"),
        ],
    },

    # ── GitHub ────────────────────────────────────────────────────────────
    "github": {
        "search": [
            Route("gh CLI", "CLI",
                  'gh search repos "{query}" --limit {n} '
                  "--json name,description,stargazerCount,url"),
            _api("github", "search", needs_query=True),
        ],
    },

    # ── YouTube ───────────────────────────────────────────────────────────
    "youtube": {
        "search": [
            Route("yt-dlp", "CLI",
                  'yt-dlp --dump-json --flat-playlist "ytsearch{n}:{query}"'),
        ],
        "detail": [
            Route("yt-dlp", "CLI", 'yt-dlp --dump-json "{query}"'),
        ],
    },

    # ── V2EX ──────────────────────────────────────────────────────────────
    "v2ex": {
        "hot": [_api("v2ex", "hot")],
        "rank": [_api("v2ex", "rank")],
    },

    # ── Twitter / X ───────────────────────────────────────────────────────
    "twitter": {
        "search": [
            Route("twitter-cli", "CLI", 'twitter search "{query}" -n {n}'),
        ],
    },

    # ── Reddit ────────────────────────────────────────────────────────────
    "reddit": {
        "search": [
            Route("OpenCLI", "MCP", 'opencli reddit search "{query}" -f yaml'),
            Route("rdt-cli", "CLI", 'rdt search "{query}" -n {n}'),
        ],
    },

    # ── 小红书 ─────────────────────────────────────────────────────────────
    "xiaohongshu": {
        "search": [
            Route("OpenCLI", "MCP",
                  'opencli xiaohongshu search "{query}" -f yaml'),
        ],
    },
}

# LLM 可能给出的 route_kind 同义词归一化
_KIND_ALIASES = {
    "ranking": "rank", "rank": "rank", "hot100": "rank", "top": "rank",
    "leaderboard": "rank", "chart": "rank",
    "hot": "hot", "popular": "hot", "trending": "hot",
    "search": "search", "query": "search", "keyword": "search",
    "detail": "detail", "video": "detail", "info": "detail",
}


def get_routes(platform: str, route_kind: str) -> list[Route]:
    """返回该平台 + 意图对应的降级链路；找不到时回退到可用链路。"""
    platform_routes = ROUTES.get(platform)
    if not platform_routes:
        return []

    kind = _KIND_ALIASES.get((route_kind or "").lower(), "search")
    if kind in platform_routes:
        return platform_routes[kind]
    # rank/hot 在部分平台等价，互为兜底
    for alt in ("rank", "hot", "search"):
        if alt in platform_routes:
            return platform_routes[alt]
    return []


def known_platforms() -> list[str]:
    return sorted(ROUTES)
