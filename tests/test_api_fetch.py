# -*- coding: utf-8 -*-
"""
Tests for core/api_fetch.py — 三级链路中的 API 兜底层。

HTTP 全部 mock，离线可跑，不产生真实网络请求。

Run with:  pytest tests/test_api_fetch.py -v
"""

import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import api_fetch
from core.api_fetch import FetchError, fetch


# ── fixtures ──────────────────────────────────────────────────────────────────

def _bili_page(n: int, start: int = 0) -> dict:
    """构造一页 B站 popular 响应。"""
    return {
        "code": 0,
        "data": {
            "list": [
                {
                    "title": f"视频{start + i}",
                    "tname": "游戏" if i % 2 else "生活",
                    "bvid": f"BV{start + i}",
                    "owner": {"name": f"UP{start + i}"},
                    "stat": {"view": 1000 + i, "like": 100 + i},
                }
                for i in range(n)
            ]
        },
    }


def _risk(code: int = -352) -> dict:
    """B站风控限流响应。"""
    return {"code": code, "message": "blocked"}


@pytest.fixture(autouse=True)
def _no_warmup():
    """屏蔽真实的首页 Cookie 预热请求。"""
    with patch.object(api_fetch, "_bili_warmup", return_value=None):
        yield


# ── bilibili rank：ranking/v2 优先，popular 兜底 ──────────────────────────────

def test_rank_uses_ranking_v2_in_one_request():
    """ranking/v2 单次即返回 100 条榜单，不该再翻页。"""
    with patch.object(api_fetch, "_http_json", return_value=_bili_page(100)) as m:
        result = fetch("bilibili", "rank", "", 100)

    assert result["returned"] == 100
    assert m.call_count == 1
    assert "ranking/v2" in result["source"]


def test_rank_falls_back_to_popular_on_rate_limit():
    """ranking/v2 被限流（-352）时退到 popular 翻页，而非整体失败。"""
    responses = [_risk(-352)] + [_bili_page(20)] * 5
    with patch.object(api_fetch, "_http_json", side_effect=responses) as m:
        result = fetch("bilibili", "rank", "", 100)

    assert result["returned"] == 100
    assert "popular" in result["source"]
    assert m.call_count == 6          # 1 次 ranking/v2 + 5 页 popular


def test_popular_fallback_stops_on_empty_page():
    """popular 翻页遇空列表应停止，而不是死循环到页数上限。"""
    responses = [_risk(), _bili_page(20), _bili_page(0)]
    with patch.object(api_fetch, "_http_json", side_effect=responses) as m:
        result = fetch("bilibili", "rank", "", 100)

    assert result["returned"] == 20
    assert m.call_count == 3


def test_rank_trims_to_requested_n():
    with patch.object(api_fetch, "_http_json", return_value=_bili_page(100)):
        assert fetch("bilibili", "rank", "", 30)["returned"] == 30


def test_rank_preserves_category_field():
    """category（tname）是分区统计的关键字段，不能丢。"""
    with patch.object(api_fetch, "_http_json", return_value=_bili_page(20)):
        items = fetch("bilibili", "rank", "", 20)["items"]

    assert all(i["category"] for i in items)
    assert {i["category"] for i in items} == {"游戏", "生活"}


def test_rank_assigns_sequential_ranks():
    with patch.object(api_fetch, "_http_json", return_value=_bili_page(20)):
        items = fetch("bilibili", "rank", "", 20)["items"]
    assert [i["rank"] for i in items] == list(range(1, 21))


# ── bilibili：风控错误码 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("code", [-352, -412, -400])
def test_bili_check_raises_on_nonzero_code(code):
    """HTTP 200 但 code 非 0 必须抛错，交由上层降级。"""
    with pytest.raises(FetchError, match=str(code)):
        api_fetch._bili_check(_risk(code), "http://x")


def test_risk_control_error_mentions_rate_limit():
    """-352 是间歇限流而非缺 WBI 签名，提示语不能误导排查方向。"""
    with pytest.raises(FetchError, match="限流"):
        api_fetch._bili_check(_risk(-352), "http://x")


def test_rank_raises_only_when_both_endpoints_fail():
    with patch.object(api_fetch, "_http_json", return_value=_risk()):
        with pytest.raises(FetchError, match="均未返回"):
            fetch("bilibili", "rank", "", 20)


# ── bilibili search ───────────────────────────────────────────────────────────

_SEARCH_RESP = {
    "code": 0,
    "data": {
        "result": [
            {"result_type": "bili_user", "data": [{"uname": "someone"}]},
            {
                "result_type": "video",
                "data": [
                    {
                        "title": '包含<em class="keyword">关键词</em>的标题',
                        "typename": "游戏",
                        "author": "UP主",
                        "bvid": "BV1",
                        "play": 5000,
                    }
                ],
            },
        ]
    },
}


def test_search_picks_video_group():
    """search/all/v2 按类型分组，必须挑出 video 组而非第一组。"""
    with patch.object(api_fetch, "_http_json", return_value=_SEARCH_RESP):
        result = fetch("bilibili", "search", "关键词", 10)
    assert result["returned"] == 1
    assert result["items"][0]["author"] == "UP主"


def test_search_strips_highlight_markup():
    """B站搜索标题带 <em> 高亮标签，会污染 LLM 输入，需要剥掉。"""
    with patch.object(api_fetch, "_http_json", return_value=_SEARCH_RESP):
        title = fetch("bilibili", "search", "关键词", 10)["items"][0]["title"]
    assert title == "包含关键词的标题"
    assert "<em" not in title


def test_search_requires_query():
    with pytest.raises(FetchError, match="query"):
        fetch("bilibili", "search", "", 10)


def test_search_raises_when_no_video_group():
    resp = {"code": 0, "data": {"result": [{"result_type": "bili_user", "data": []}]}}
    with patch.object(api_fetch, "_http_json", return_value=resp):
        with pytest.raises(FetchError, match="无结果"):
            fetch("bilibili", "search", "x", 10)


# ── v2ex ──────────────────────────────────────────────────────────────────────

def test_v2ex_hot_maps_node_to_category():
    resp = [
        {"title": "话题A", "node": {"title": "程序员"}, "replies": 5, "url": "u1"},
        {"title": "话题B", "node": {"title": "分享创造"}, "replies": 9, "url": "u2"},
    ]
    with patch.object(api_fetch, "_http_json", return_value=resp):
        result = fetch("v2ex", "hot", "", 10)

    assert result["returned"] == 2
    assert result["items"][0]["category"] == "程序员"


def test_v2ex_respects_n_limit():
    resp = [{"title": f"t{i}", "node": {"title": "n"}, "replies": 0, "url": "u"}
            for i in range(50)]
    with patch.object(api_fetch, "_http_json", return_value=resp):
        assert fetch("v2ex", "hot", "", 5)["returned"] == 5


def test_v2ex_raises_on_non_list():
    with patch.object(api_fetch, "_http_json", return_value={}):
        with pytest.raises(FetchError):
            fetch("v2ex", "hot", "", 10)


# ── github ────────────────────────────────────────────────────────────────────

def test_github_maps_language_to_category():
    resp = {"items": [{
        "full_name": "owner/repo", "description": "desc",
        "language": "Python", "stargazers_count": 42,
        "html_url": "https://github.com/owner/repo",
    }]}
    with patch.object(api_fetch, "_http_json", return_value=resp):
        item = fetch("github", "search", "cli", 10)["items"][0]

    assert item["category"] == "Python"
    assert item["stars"] == 42


def test_github_requires_query():
    with pytest.raises(FetchError, match="query"):
        fetch("github", "search", "", 10)


def test_github_raises_on_empty_results():
    with patch.object(api_fetch, "_http_json", return_value={"items": []}):
        with pytest.raises(FetchError, match="无结果"):
            fetch("github", "search", "zzz", 10)


# ── 分发与 CLI ────────────────────────────────────────────────────────────────

def test_unsupported_platform_raises():
    with pytest.raises(FetchError, match="不支持"):
        fetch("nosuchsite", "rank", "", 10)


def test_hot_and_rank_share_bilibili_handler():
    with patch.object(api_fetch, "_http_json", return_value=_bili_page(20)):
        a = fetch("bilibili", "rank", "", 20)
        b = fetch("bilibili", "hot", "", 20)
    assert a["items"] == b["items"]


def test_main_returns_zero_and_emits_json(capsysbinary):
    with patch.object(api_fetch, "_http_json", return_value=_bili_page(20)):
        rc = api_fetch.main(["bilibili", "rank", "--n", "20"])

    assert rc == 0
    # 必须是 UTF-8 输出，否则中文标题在 Windows 控制台会乱码
    payload = json.loads(capsysbinary.readouterr().out.decode("utf-8"))
    assert payload["returned"] == 20


def test_main_returns_one_on_failure(capsysbinary):
    with patch.object(api_fetch, "_http_json",
                      return_value={"code": -352, "message": "blocked"}):
        rc = api_fetch.main(["bilibili", "rank", "--n", "20"])
    assert rc == 1


def test_http_error_becomes_fetch_error():
    """请求走 _opener（维持 CookieJar），不是裸 urlopen。"""
    import urllib.error
    err = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
    with patch.object(api_fetch._opener, "open", side_effect=err):
        with pytest.raises(FetchError, match="403"):
            api_fetch._http_json("https://example.com")


def test_network_error_becomes_fetch_error():
    import urllib.error
    with patch.object(api_fetch._opener, "open",
                      side_effect=urllib.error.URLError("timed out")):
        with pytest.raises(FetchError, match="网络不可达"):
            api_fetch._http_json("https://example.com")


def test_opener_carries_cookie_processor():
    """CookieJar 是绕过 B站风控的关键，不能退回裸 urlopen。"""
    import urllib.request
    assert any(
        isinstance(h, urllib.request.HTTPCookieProcessor)
        for h in api_fetch._opener.handlers
    )
