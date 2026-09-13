# -*- coding: utf-8 -*-
"""
总控 Master Agent — 基于 Claude Opus 5

职责：
1. 分析用户意图，输出 QUERY_ONLY / ACTION_REQUIRED 分类
2. QUERY_ONLY：按 CLI → MCP/OpenCLI → API 三级链路尝试 Agent-Reach
3. 三级全败 或 ACTION_REQUIRED：降级/直连 browser-use
4. 通过 status_cb 回调向 Streamlit 实时推送状态
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from typing import Callable

# 保证项目根目录与两个原生项目均可导入
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in [_PROJECT_ROOT, "D:/Code/browser-use", "D:/Code/agent-reach/Agent-Reach"]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config import get_llm, get_browser_llm  # noqa: E402

from .backend_resolver import order_routes  # noqa: E402
from .routes import get_routes, known_platforms  # noqa: E402

logger = logging.getLogger(__name__)

# 默认抓取条数（B站 Hot100 这类需求要拿满 100 条）
_DEFAULT_N = 20
_MAX_N = 100


# ── 意图分析 ──────────────────────────────────────────────────────────────────

def _intent_prompt(user_input: str) -> str:
    # 用拼接而非 .format，避免用户输入中的花括号触发 KeyError
    return (
        "你是任务分类 Agent。分析用户输入，只输出一个合法 JSON 对象，不要有其他文字。\n\n"
        "字段：\n"
        '- task_type: "QUERY_ONLY"（只检索/查询数据）或 '
        '"ACTION_REQUIRED"（需在网页上登录/填表/点击/提交）\n'
        "- platform: 目标平台，取值限定为 "
        + "/".join(known_platforms()) + "/web/unknown\n"
        '- route_kind: 检索意图，取值 "rank"（排行榜/榜单/Hot100）、'
        '"hot"（热门推荐）、"search"（关键词搜索）、"detail"（单个视频/仓库详情）\n'
        "- query: 搜索关键词，或 detail 场景下的 ID/URL；无关键词时为空字符串\n"
        "- n: 需要的条目数量（整数）。用户说 Hot100/前100 则为 100，未指明则 20\n"
        "- browser_task: 若需浏览器执行，给 browser-use 的完整中文任务描述\n\n"
        "用户输入：" + user_input
    )


def classify_intent(user_input: str) -> dict:
    """调用 Claude Opus 5 分析意图，返回结构化 JSON。"""
    llm = get_llm()
    response = llm.invoke(_intent_prompt(user_input))
    text = response.content if hasattr(response, "content") else str(response)
    text = text.strip()

    # 容错：剥掉 ``` 围栏，或从混杂文本里抠出第一个 JSON 对象
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) > 1:
            text = parts[1]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    text = text.strip()
    if not text.startswith("{"):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group(0)

    return json.loads(text)


# ── 总控主函数 ────────────────────────────────────────────────────────────────

def run(user_input: str, status_cb: Callable[[str], None] | None = None) -> str:
    """处理用户请求，返回最终结果字符串。"""

    def emit(msg: str) -> None:
        logger.info(msg)
        if status_cb:
            status_cb(msg)

    emit("正在分析意图...")
    try:
        intent = classify_intent(user_input)
    except Exception as exc:
        emit(f"意图分析失败：{exc}，直接启动浏览器...")
        return _browser_fallback(user_input, user_input, emit)

    task_type    = intent.get("task_type", "ACTION_REQUIRED")
    platform     = (intent.get("platform") or "unknown").lower()
    route_kind   = intent.get("route_kind") or "search"
    query        = intent.get("query") or ""
    browser_task = intent.get("browser_task") or user_input

    try:
        n = max(1, min(int(intent.get("n") or _DEFAULT_N), _MAX_N))
    except (TypeError, ValueError):
        n = _DEFAULT_N

    emit(f"意图：{task_type} | 平台：{platform} | 类型：{route_kind} | 条数：{n}")

    if task_type == "ACTION_REQUIRED":
        emit("操作类任务，绕过 Agent-Reach，直连 browser-use...")
        return _browser_fallback(browser_task, user_input, emit)

    # ── QUERY_ONLY：三级降级链路 ──
    routes = get_routes(platform, route_kind)
    if routes:
        # 先问 Agent-Reach 哪一级后端是活的，把它提到最前（不裁掉其余级）
        routes, probe_msg = order_routes(routes, platform)
        emit(probe_msg)
        emit(f"匹配到 {platform} 路由，共 {len(routes)} 级链路，依次尝试")
        from .bash_executor import run_command, BashError

        for route in routes:
            cmd = (
                route.command
                .replace("{query}", query.replace('"', '\\"'))
                .replace("{n}", str(n))
            )
            emit(f"  [{route.tier}] {route.label} → {cmd}")
            try:
                raw = run_command(cmd)
            except BashError as exc:
                emit(f"  ✗ {route.label} 失败：{exc}")
                continue

            if not _looks_usable(raw):
                emit(f"  ✗ {route.label} 返回空数据或接口报错，继续下一级...")
                continue

            emit(f"  ✅ {route.label} 成功（{len(raw)} 字节），交由 Opus 5 分析...")
            return _summarize(user_input, raw, emit)

        emit("三级链路全部失败，降级到 browser-use 视觉攻坚...")
    else:
        emit(f"Agent-Reach 无 {platform} 路由，直接启动 browser-use...")

    return _browser_fallback(browser_task, user_input, emit)


_EMPTY = (None, [], {}, "")


def _looks_usable(raw: str) -> bool:
    """
    退出码 0 不代表真拿到数据，这里做二次判定：
      - 空输出 → 无效
      - 非 JSON（bili-cli / gh 的表格文本）→ 非空即有效
      - B站原始响应 {code, data}：code != 0 即业务失败（-352/-412 风控）
      - api_fetch 包装结果 {source, returned, items}：看 items 是否为空
    """
    if not raw or len(raw.strip()) < 2:
        return False

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return True  # 非 JSON 的 CLI 输出，非空就算有效

    if isinstance(data, list):
        return bool(data)

    if not isinstance(data, dict):
        return True

    # B站/部分国内接口：HTTP 200 但 code 非 0 表示业务失败
    if data.get("code") not in (None, 0):
        return False

    # 依次检查两种数据载荷形状，任一存在且非空即有效
    for key in ("items", "data"):
        if key in data:
            return data[key] not in _EMPTY

    return True


# ── LLM 汇总 ──────────────────────────────────────────────────────────────────

def _summarize(question: str, raw_data: str, emit: Callable) -> str:
    emit("正在用 Claude Opus 5 分析原始数据...")
    llm = get_llm()
    limit = 60000  # Opus 5 上下文充裕，不必压到 12k，避免 Hot100 数据被截断
    truncated = raw_data[:limit] + ("\n...[数据已截断]" if len(raw_data) > limit else "")
    prompt = (
        "用户的问题是：" + question + "\n\n"
        "以下是通过 API/CLI 获取的原始数据：\n" + truncated + "\n\n"
        "请基于原始数据用中文回答，给出明确的统计结论。"
        "若数据含分区/类别字段，请给出各类别的数量与占比排序。"
    )
    response = llm.invoke(prompt)
    return response.content if hasattr(response, "content") else str(response)


# ── browser-use 降级 ──────────────────────────────────────────────────────────

# 模型反复把 evaluate 的参数名写成 javascript/js_code（正确字段是 code），
# 触发 46 条 union 校验错误并在 5 次后终止 Agent。该任务无需执行 JS，
# 直接从工具集中摘掉 evaluate，从根上消除这个失败模式。
_EXCLUDED_ACTIONS = ["evaluate"]

_EXTEND_SYSTEM_MSG = (
    "数据采集类任务的要求：\n"
    "1. 优先使用 extract 动作从页面结构化提取数据，不要用 JS 求值。\n"
    "2. 榜单/列表页需要反复 scroll 到底，确保拿满用户要求的条目数后再 done。\n"
    "3. 每个条目都要带上其分区/类别标签；页面未直接显示时才进详情页。\n"
    "4. 只有在数据确实采集完整时才调用 done 并置 success=true。"
)


def _browser_fallback(browser_task: str, original_question: str, emit: Callable) -> str:
    """启动 browser-use Agent 执行浏览器任务，返回结果字符串。"""
    from browser_use.agent.service import Agent
    from browser_use.browser.profile import BrowserProfile
    from browser_use.tools.service import Tools

    emit("正在启动 Playwright 浏览器（视觉模式）...")
    llm = get_browser_llm()

    async def _run() -> str:
        agent = Agent(
            task=browser_task,
            llm=llm,
            use_vision=True,
            browser_profile=BrowserProfile(headless=False),
            tools=Tools(exclude_actions=_EXCLUDED_ACTIONS),
            extend_system_message=_EXTEND_SYSTEM_MSG,
            # 格式错误可自愈，给足重试余量，别 5 次就放弃
            max_failures=10,
            # 输出格式出错时用同一个 Opus 5 实例重试，避免直接终止
            fallback_llm=llm,
            max_actions_per_step=3,
            use_judge=False,   # 评审只产出日志噪音，不影响交付结果
        )
        history = await agent.run(max_steps=40)
        return history.final_result() or "（浏览器 Agent 未返回结果）"

    try:
        try:
            import nest_asyncio
            nest_asyncio.apply()
            result = asyncio.get_event_loop().run_until_complete(_run())
        except ImportError:
            result = asyncio.run(_run())
    except Exception as exc:
        emit(f"浏览器 Agent 执行失败：{exc}")
        return f"执行失败：{exc}"

    emit("浏览器 Agent 执行完毕，交由 Claude Opus 5 整理结果...")
    return _summarize(original_question, result, emit)
