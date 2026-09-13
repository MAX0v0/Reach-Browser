# -*- coding: utf-8 -*-
"""
Tests for core/master_agent.py — 意图分类、三级降级链路、数据有效性校验。

LLM 调用全部 mock，离线可跑，不需要 API Key，也不会真的开浏览器。

Run with:  pytest tests/test_master_agent.py -v
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── helpers ───────────────────────────────────────────────────────────────────

def _resp(text: str):
    """构造 LangChain AIMessage 风格的 mock 响应。"""
    msg = MagicMock()
    msg.content = text
    return msg


def _seq_llm(*responses):
    """按调用顺序依次返回多个响应的 mock LLM。"""
    llm = MagicMock()
    llm.invoke.side_effect = [_resp(r) for r in responses]
    return llm


def _keep_default_order(monkeypatch, module):
    """
    固定链路顺序为 CLI→MCP→API。

    order_routes 会真的调用 Agent-Reach 体检（含网络请求），顺序随本机装了哪些
    CLI 而变；降级链路的测试必须与环境无关。
    """
    monkeypatch.setattr(
        module, "order_routes", lambda routes, platform: (routes, "（测试固定顺序）")
    )


def _patch_llm(monkeypatch, module, *responses):
    """把 get_llm 固定为同一个 mock 实例。

    注意不能写 lambda: _seq_llm(...)——那样每次 get_llm() 都新建 mock，
    side_effect 会从头开始，第二次调用（汇总）就会错拿意图 JSON。
    """
    llm = _seq_llm(*responses)
    monkeypatch.setattr(module, "get_llm", lambda: llm)
    return llm


_BILI_RANK_INTENT = (
    '{"task_type":"QUERY_ONLY","platform":"bilibili","route_kind":"rank",'
    '"query":"","n":100,"browser_task":"访问B站排行榜统计分区"}'
)


# ── _intent_prompt ────────────────────────────────────────────────────────────

def test_intent_prompt_contains_user_input():
    from core.master_agent import _intent_prompt
    assert "查询B站热榜" in _intent_prompt("查询B站热榜")


def test_intent_prompt_handles_braces():
    """用户输入含花括号不能触发 KeyError。"""
    from core.master_agent import _intent_prompt
    assert "{key}" in _intent_prompt("give me {key}")


def test_intent_prompt_lists_known_platforms():
    from core.master_agent import _intent_prompt
    assert "bilibili" in _intent_prompt("x")


# ── classify_intent ───────────────────────────────────────────────────────────

def test_classify_parses_plain_json(monkeypatch):
    from core import master_agent
    monkeypatch.setattr(master_agent, "get_llm", lambda: _seq_llm(_BILI_RANK_INTENT))
    result = master_agent.classify_intent("B站Hot100")
    assert result["route_kind"] == "rank"
    assert result["n"] == 100


def test_classify_strips_markdown_fence(monkeypatch):
    from core import master_agent
    monkeypatch.setattr(
        master_agent, "get_llm",
        lambda: _seq_llm("```json\n" + _BILI_RANK_INTENT + "\n```"),
    )
    assert master_agent.classify_intent("x")["platform"] == "bilibili"


def test_classify_extracts_json_from_prose(monkeypatch):
    """模型在 JSON 前后夹杂说明文字时也要能抠出来。"""
    from core import master_agent
    monkeypatch.setattr(
        master_agent, "get_llm",
        lambda: _seq_llm("好的，分析如下：\n" + _BILI_RANK_INTENT + "\n希望有帮助"),
    )
    assert master_agent.classify_intent("x")["platform"] == "bilibili"


# ── _looks_usable ─────────────────────────────────────────────────────────────

def test_looks_usable_rejects_empty():
    from core.master_agent import _looks_usable
    assert not _looks_usable("")
    assert not _looks_usable("   ")


def test_looks_usable_rejects_bilibili_risk_control():
    """B站风控返回 code=-412，退出码是 0，必须靠 code 判失败。"""
    from core.master_agent import _looks_usable
    assert not _looks_usable('{"code":-412,"message":"request blocked"}')


def test_looks_usable_rejects_empty_data_payload():
    from core.master_agent import _looks_usable
    assert not _looks_usable('{"code":0,"data":[]}')


def test_looks_usable_accepts_real_payload():
    from core.master_agent import _looks_usable
    assert _looks_usable('{"code":0,"data":{"list":[{"title":"video"}]}}')


def test_looks_usable_accepts_non_json_cli_output():
    """bili-cli 输出的是表格文本，不是 JSON，非空即有效。"""
    from core.master_agent import _looks_usable
    assert _looks_usable("1. 某视频  UP主  游戏区")


# ── run: QUERY_ONLY 三级链路 ──────────────────────────────────────────────────

def test_run_succeeds_on_first_tier(monkeypatch):
    """CLI 层就成功时，不应继续尝试 MCP/API。"""
    from core import master_agent
    _patch_llm(monkeypatch, master_agent, _BILI_RANK_INTENT, "游戏区占比最高")
    with patch("core.bash_executor.run_command",
               return_value="1. 视频A 游戏区\n2. 视频B 生活区") as mock_run:
        msgs = []
        result = master_agent.run("B站Hot100分析", status_cb=msgs.append)

    assert result == "游戏区占比最高"
    assert mock_run.call_count == 1
    assert any("bili rank" in m for m in msgs)


def test_run_falls_through_to_api_tier(monkeypatch):
    """CLI 缺失 + MCP 缺失时，应降到 API 层并成功。"""
    from core import master_agent
    from core.bash_executor import BashError

    _patch_llm(monkeypatch, master_agent, _BILI_RANK_INTENT, "已统计分区")
    _keep_default_order(monkeypatch, master_agent)

    def _side_effect(cmd, **kw):
        if cmd.startswith("bili") or cmd.startswith("opencli"):
            raise BashError("command not found")
        return '{"code":0,"data":{"list":[{"title":"v","tname":"游戏"}]}}'

    with patch("core.bash_executor.run_command", side_effect=_side_effect) as mock_run:
        msgs = []
        result = master_agent.run("B站Hot100分析", status_cb=msgs.append)

    assert result == "已统计分区"
    assert mock_run.call_count == 3          # CLI → MCP → API
    assert any("[API]" in m for m in msgs)


def test_run_skips_tier_returning_risk_control(monkeypatch):
    """某层退出码为 0 但返回风控错误码，应继续降级而非误判成功。"""
    from core import master_agent
    from core.bash_executor import BashError

    _patch_llm(monkeypatch, master_agent, _BILI_RANK_INTENT, "ok")
    _keep_default_order(monkeypatch, master_agent)

    calls = []

    def _side_effect(cmd, **kw):
        calls.append(cmd)
        if cmd.startswith("bili"):
            return '{"code":-412,"message":"blocked"}'   # 风控，非空但无效
        if cmd.startswith("opencli"):
            raise BashError("not installed")
        return '{"code":0,"data":{"list":[{"title":"v"}]}}'

    with patch("core.bash_executor.run_command", side_effect=_side_effect):
        msgs = []
        master_agent.run("B站Hot100", status_cb=msgs.append)

    assert len(calls) == 3
    assert any("接口报错" in m or "空数据" in m for m in msgs)


def test_run_falls_back_to_browser_when_all_tiers_fail(monkeypatch):
    from core import master_agent
    from core.bash_executor import BashError

    monkeypatch.setattr(master_agent, "get_llm", lambda: _seq_llm(_BILI_RANK_INTENT))

    with patch("core.bash_executor.run_command", side_effect=BashError("fail")):
        with patch.object(master_agent, "_browser_fallback",
                          return_value="浏览器结果") as mock_fb:
            msgs = []
            result = master_agent.run("B站Hot100", status_cb=msgs.append)

    mock_fb.assert_called_once()
    assert result == "浏览器结果"
    assert any("降级" in m for m in msgs)


# ── run: ACTION_REQUIRED ──────────────────────────────────────────────────────

def test_run_action_required_skips_bash(monkeypatch):
    from core import master_agent

    intent = (
        '{"task_type":"ACTION_REQUIRED","platform":"web","route_kind":"search",'
        '"query":"","n":20,"browser_task":"登录并填表"}'
    )
    monkeypatch.setattr(master_agent, "get_llm", lambda: _seq_llm(intent))

    with patch("core.bash_executor.run_command") as mock_run:
        with patch.object(master_agent, "_browser_fallback",
                          return_value="操作完成") as mock_fb:
            result = master_agent.run("帮我登录并填表")

    mock_run.assert_not_called()
    mock_fb.assert_called_once()
    assert result == "操作完成"


def test_run_falls_back_on_bad_intent_json(monkeypatch):
    from core import master_agent
    monkeypatch.setattr(master_agent, "get_llm", lambda: _seq_llm("这不是JSON"))

    with patch.object(master_agent, "_browser_fallback", return_value="兜底结果"):
        msgs = []
        result = master_agent.run("任意输入", status_cb=msgs.append)

    assert result == "兜底结果"
    assert any("失败" in m for m in msgs)


# ── n 参数处理 ────────────────────────────────────────────────────────────────

def test_n_is_substituted_into_command(monkeypatch):
    from core import master_agent
    _patch_llm(monkeypatch, master_agent, _BILI_RANK_INTENT, "ok")
    with patch("core.bash_executor.run_command", return_value="data") as mock_run:
        master_agent.run("B站Hot100")

    assert "-n 100" in mock_run.call_args[0][0]


def test_n_is_clamped_to_max(monkeypatch):
    from core import master_agent
    intent = _BILI_RANK_INTENT.replace('"n":100', '"n":99999')
    _patch_llm(monkeypatch, master_agent, intent, "ok")

    with patch("core.bash_executor.run_command", return_value="data") as mock_run:
        master_agent.run("B站")

    assert "-n 100" in mock_run.call_args[0][0]


def test_invalid_n_uses_default(monkeypatch):
    from core import master_agent
    intent = _BILI_RANK_INTENT.replace('"n":100', '"n":"abc"')
    _patch_llm(monkeypatch, master_agent, intent, "ok")

    with patch("core.bash_executor.run_command", return_value="data") as mock_run:
        master_agent.run("B站")

    assert "-n 20" in mock_run.call_args[0][0]


# ── _summarize ────────────────────────────────────────────────────────────────

def test_summarize_truncates_very_long_data(monkeypatch):
    from core import master_agent
    captured = {}
    llm = MagicMock()
    llm.invoke.side_effect = lambda p: (captured.__setitem__("p", p), _resp("结论"))[1]
    monkeypatch.setattr(master_agent, "get_llm", lambda: llm)

    master_agent._summarize("问题", "x" * 100000, lambda m: None)
    assert "[数据已截断]" in captured["p"]


def test_summarize_keeps_hot100_sized_data(monkeypatch):
    """Hot100 量级的数据（约 3 万字节）不应被截断。"""
    from core import master_agent
    captured = {}
    llm = MagicMock()
    llm.invoke.side_effect = lambda p: (captured.__setitem__("p", p), _resp("结论"))[1]
    monkeypatch.setattr(master_agent, "get_llm", lambda: llm)

    master_agent._summarize("问题", "y" * 30000, lambda m: None)
    assert "[数据已截断]" not in captured["p"]
