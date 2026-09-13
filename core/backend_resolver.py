# -*- coding: utf-8 -*-
"""
Agent-Reach 后端解析器 — 复用 Agent-Reach 自己的 channel 体检来决定链路顺序。

这是 Agent-Reach 的"正宗用法"（参见其 summarize.py）：不盲试所有后端，
而是先问 channel 自己哪个后端当前可用：

    ch = BilibiliChannel()
    status, msg = ch.check(Config())
    ch.active_backend   # → 'bili-cli' / 'OpenCLI' / 'B站搜索 API' / None

对应 requirement.md 的「Master Agent 检索 Agent-Reach 的路由定义」。

注意 check() 只是建议而非保证：Agent-Reach 的文档明确说 doctor 不执行平台
命令，`active_backend` 不等于目标请求一定成功（例如 B站 API 会间歇限流）。
所以这里只用它给链路排序，不用它裁掉任何一级——真实可用性仍由实际执行判定。
"""

from __future__ import annotations

import logging
import sys

_AGENT_REACH_PATH = "D:/Code/agent-reach/Agent-Reach"
if _AGENT_REACH_PATH not in sys.path:
    sys.path.insert(0, _AGENT_REACH_PATH)

logger = logging.getLogger(__name__)

# Agent-Reach 的 backend 标签 → 本项目的 tier 名
_BACKEND_TO_TIER = {
    "bili-cli": "CLI",
    "gh": "CLI",
    "gh CLI": "CLI",
    "yt-dlp": "CLI",
    "twitter-cli": "CLI",
    "rdt-cli": "CLI",
    "xhs-cli": "CLI",
    "OpenCLI": "MCP",
    "xiaohongshu-mcp": "MCP",
    "mcporter": "MCP",
    "B站搜索 API": "API",
    "Exa": "API",
    "内置": "API",
}


def _tier_of(backend: str | None) -> str | None:
    """把 Agent-Reach 的后端标签归一化到 CLI/MCP/API。"""
    if not backend:
        return None
    if backend in _BACKEND_TO_TIER:
        return _BACKEND_TO_TIER[backend]
    # 前缀兜底：上游可能带版本或补充说明
    for label, tier in _BACKEND_TO_TIER.items():
        if backend.startswith(label):
            return tier
    lowered = backend.lower()
    if "api" in lowered:
        return "API"
    if "cli" in lowered and "open" in lowered:
        return "MCP"
    if "cli" in lowered:
        return "CLI"
    if "mcp" in lowered:
        return "MCP"
    return None


def resolve_active_tier(platform: str) -> tuple[str | None, str]:
    """
    问 Agent-Reach：该平台当前哪一级后端是活的。

    Returns:
        (tier, message) — tier 为 'CLI'/'MCP'/'API' 或 None（无法判定）；
        message 是给用户看的可读说明。
    """
    try:
        from agent_reach.channels import get_channel
        from agent_reach.config import Config
    except ImportError as exc:
        return None, f"Agent-Reach 不可导入（{exc}），按默认顺序尝试"

    channel = get_channel(platform)
    if channel is None:
        return None, f"Agent-Reach 无 {platform} channel，按默认顺序尝试"

    try:
        status, msg = channel.check(Config())
    except Exception as exc:  # noqa: BLE001 — 体检失败不该阻断主流程
        return None, f"{platform} 体检异常（{exc}），按默认顺序尝试"

    backend = getattr(channel, "active_backend", None)
    tier = _tier_of(backend)
    if tier is None:
        return None, f"{platform} 体检 status={status}，未给出可用后端：{msg[:80]}"

    return tier, f"Agent-Reach 体检：{platform} 活跃后端 = {backend}（{tier} 级）"


def order_routes(routes: list, platform: str) -> tuple[list, str]:
    """
    按 Agent-Reach 的体检结果重排链路，把活跃那一级提到最前。

    不删除任何一级：体检只是建议，实际可用性由执行结果判定。
    """
    tier, message = resolve_active_tier(platform)
    if tier is None:
        return routes, message

    preferred = [r for r in routes if r.tier == tier]
    rest = [r for r in routes if r.tier != tier]
    if not preferred:
        return routes, message + "（本项目无该级路由，按默认顺序）"
    return preferred + rest, message
