# -*- coding: utf-8 -*-
"""
API 直连抓取器 — 三级降级链路的最后一级（tier: API）。

为什么不直接拼 curl：
  1. bash_executor 在 Windows 上走 cmd.exe，`/tmp/xxx`、`/dev/null` 全部无效
     （实测 curl 退出码 23 = 写文件失败）。
  2. 分页需要循环，单条 shell 命令做不到（B站 popular 单页上限 20 条，
     取 Hot100 必须翻 5 页）。
  3. 业务错误码需要在拿到数据后判断，shell 里没法优雅处理。

用法（由 routes.py 组装成命令行调用）：
    python -m core.api_fetch bilibili rank --n 100
    python -m core.api_fetch bilibili search --query "关键词" --n 20
    python -m core.api_fetch v2ex hot
    python -m core.api_fetch github search --query "python" --n 20

始终把结果以 JSON 打到 stdout；失败时非零退出码 + stderr 说明，
交由 Master Agent 触发下一级降级。
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

# 与 Agent-Reach summarize.py / references/video.md 保持一致的 UA
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_BILI_REF = "https://www.bilibili.com/"
_TIMEOUT = 25


class FetchError(RuntimeError):
    """抓取失败，消息会写入 stderr 供 Master Agent 记录降级原因。"""


# ── 底层 HTTP ─────────────────────────────────────────────────────────────────
# 必须用 CookieJar + HTTPCookieProcessor 维持会话（对应 video.md 的 curl -c/-b）。
# 手工解析 Set-Cookie 头再拼 Cookie 字符串会丢 cookie，实测导致 B站返回
# code=-352 风控拦截；用 opener 维持会话后 ranking/v2 正常返回 code=0。
_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
)


def _http_json(url: str, referer: str = "") -> dict:
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    if referer:
        headers["Referer"] = referer

    req = urllib.request.Request(url, headers=headers)
    try:
        with _opener.open(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} — {url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FetchError(f"网络不可达：{exc} — {url}") from exc
    except json.JSONDecodeError as exc:
        raise FetchError(f"响应不是合法 JSON — {url}") from exc


def _bili_warmup() -> None:
    """先访问首页让 CookieJar 收下风控 Cookie；失败不阻断，直接试接口。"""
    try:
        req = urllib.request.Request(_BILI_REF, headers={"User-Agent": _UA})
        _opener.open(req, timeout=10).read(1)
    except Exception:  # noqa: BLE001 — 预热失败不应中断主流程
        pass


def _bili_check(payload: dict, url: str) -> dict:
    """B站 HTTP 200 也可能是业务失败，必须查 code。"""
    code = payload.get("code")
    if code not in (0, None):
        msg = payload.get("message") or payload.get("msg") or ""
        hint = ""
        if code in (-352, -412):
            hint = "（风控限流：请求过于频繁或该 IP 被临时拦截，稍后可恢复）"
        raise FetchError(f"B站接口返回 code={code} {msg}{hint} — {url}")
    return payload


# ── B站 ───────────────────────────────────────────────────────────────────────

def _bili_popular_pages(n: int) -> list[dict]:
    """popular 端点翻页取够 n 条（单页上限 20），作为 ranking/v2 的兜底。"""
    items: list[dict] = []
    page = 1
    while len(items) < n and page <= 10:
        url = (
            "https://api.bilibili.com/x/web-interface/popular"
            f"?ps=20&pn={page}"
        )
        try:
            payload = _bili_check(_http_json(url, _BILI_REF), url)
        except FetchError:
            break
        batch = (payload.get("data") or {}).get("list") or []
        if not batch:
            break
        items.extend(batch)
        page += 1
    return items


def bilibili_rank(n: int) -> dict:
    """
    全站排行榜（Hot100）。

    优先 ranking/v2：单次请求返回 100 条按 score 排序的真榜单，自带 tname 分区
    字段。但该端点受 B站 IP 级风控限流——实测同一份代码先返回 code=0/100 条，
    连续请求若干次后转为 code=-352。因此它不是稳定可用，必须留兜底。

    兜底 popular：限流下依然稳定，但单页上限 20 条需翻页，且语义是"热门推荐"
    而非榜单排名，分区分布与真榜单接近但不完全等价。
    """
    _bili_warmup()

    url = "https://api.bilibili.com/x/web-interface/ranking/v2?rid=0&type=all"
    source = "bilibili ranking/v2 API"
    try:
        payload = _bili_check(_http_json(url, _BILI_REF), url)
        items = (payload.get("data") or {}).get("list") or []
    except FetchError:
        items = []

    if not items:
        # 榜单端点不可用时退到 popular 翻页
        items = _bili_popular_pages(n)
        source = "bilibili popular API (ranking/v2 unavailable)"

    if not items:
        raise FetchError("B站 ranking/v2 与 popular 均未返回条目")

    items = items[:n]
    return {
        "source": source,
        "requested": n,
        "returned": len(items),
        # 只保留分析所需字段，避免把无关体积灌进 LLM 上下文
        "items": [
            {
                "rank": i + 1,
                "title": v.get("title"),
                "category": v.get("tname"),          # 分区名，分区统计的关键字段
                "author": (v.get("owner") or {}).get("name"),
                "bvid": v.get("bvid"),
                "views": (v.get("stat") or {}).get("view"),
                "likes": (v.get("stat") or {}).get("like"),
            }
            for i, v in enumerate(items)
        ],
    }


def bilibili_search(query: str, n: int) -> dict:
    if not query:
        raise FetchError("search 需要 query 参数")
    _bili_warmup()

    # 单页约 20 条；按需翻页并按 bvid 去重（对应 summarize.py 的 --pages）
    videos: list[dict] = []
    seen: set[str] = set()
    for page in range(1, 6):
        if len(videos) >= n:
            break
        url = (
            "https://api.bilibili.com/x/web-interface/search/all/v2?"
            + urllib.parse.urlencode({"keyword": query, "page": page})
        )
        payload = _bili_check(_http_json(url, _BILI_REF), url)

        # search/all/v2 把结果按类型分组放在 data.result 里
        batch: list[dict] = []
        for group in (payload.get("data") or {}).get("result") or []:
            if group.get("result_type") == "video":
                batch = group.get("data") or []
                break
        if not batch:
            break
        for v in batch:
            bvid = v.get("bvid") or ""
            if bvid and bvid in seen:
                continue
            if bvid:
                seen.add(bvid)
            videos.append(v)

    if not videos:
        raise FetchError(f"B站搜索无结果：{query}")

    videos = videos[:n]
    return {
        "source": "bilibili search API",
        "query": query,
        "returned": len(videos),
        "items": [
            {
                "title": (v.get("title") or "").replace('<em class="keyword">', "")
                                              .replace("</em>", ""),
                "category": v.get("typename"),
                "author": v.get("author"),
                "bvid": v.get("bvid"),
                "views": v.get("play"),
            }
            for v in videos
        ],
    }


# ── V2EX ──────────────────────────────────────────────────────────────────────

def v2ex_hot(n: int) -> dict:
    url = "https://www.v2ex.com/api/topics/hot.json"
    payload = _http_json(url)
    if not isinstance(payload, list) or not payload:
        raise FetchError("V2EX 热门接口未返回列表")
    topics = payload[:n]
    return {
        "source": "v2ex hot API",
        "returned": len(topics),
        "items": [
            {
                "title": t.get("title"),
                "category": (t.get("node") or {}).get("title"),
                "replies": t.get("replies"),
                "url": t.get("url"),
            }
            for t in topics
        ],
    }


# ── GitHub ────────────────────────────────────────────────────────────────────

def github_search(query: str, n: int) -> dict:
    if not query:
        raise FetchError("search 需要 query 参数")
    url = (
        "https://api.github.com/search/repositories"
        f"?q={urllib.parse.quote(query)}&per_page={min(n, 100)}"
    )
    payload = _http_json(url)
    repos = payload.get("items") or []
    if not repos:
        raise FetchError(f"GitHub 搜索无结果：{query}")
    return {
        "source": "github search API",
        "query": query,
        "returned": len(repos),
        "items": [
            {
                "name": r.get("full_name"),
                "description": r.get("description"),
                "category": r.get("language"),
                "stars": r.get("stargazers_count"),
                "url": r.get("html_url"),
            }
            for r in repos
        ],
    }


# ── 分发表 ────────────────────────────────────────────────────────────────────

_HANDLERS = {
    ("bilibili", "rank"):   lambda q, n: bilibili_rank(n),
    ("bilibili", "hot"):    lambda q, n: bilibili_rank(n),
    ("bilibili", "search"): lambda q, n: bilibili_search(q, n),
    ("v2ex", "hot"):        lambda q, n: v2ex_hot(n),
    ("v2ex", "rank"):       lambda q, n: v2ex_hot(n),
    ("github", "search"):   lambda q, n: github_search(q, n),
}


def fetch(platform: str, kind: str, query: str = "", n: int = 20) -> dict:
    handler = _HANDLERS.get((platform, kind))
    if handler is None:
        raise FetchError(f"API 层不支持 {platform}/{kind}")
    return handler(query, n)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="core.api_fetch")
    parser.add_argument("platform")
    parser.add_argument("kind")
    parser.add_argument("--query", default="")
    parser.add_argument("--n", type=int, default=20)
    args = parser.parse_args(argv)

    try:
        result = fetch(args.platform, args.kind, args.query, args.n)
    except FetchError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # Windows 控制台默认 GBK，显式按 UTF-8 写出避免中文标题乱码
    out = json.dumps(result, ensure_ascii=False)
    sys.stdout.buffer.write(out.encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
