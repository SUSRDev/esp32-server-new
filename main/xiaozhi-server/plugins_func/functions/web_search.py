#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多平台热度联网搜索：B站/头条/Bing/百度/抖音/小红书/UAPI，按热度与摘要质量排序。"""
from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from typing import Dict, List, Tuple
from urllib.parse import quote, quote_plus, unquote

import requests
from plugins_func.register import register_function, ToolType, ActionResponse, Action

TAG = __name__

WEB_SEARCH_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "联网搜索实时信息。用户问新闻热点、最新消息、网络梗、是谁、什么意思、怎么来的、"
            "查资料、搜索xxx、百度一下、谷歌一下、B站搜、抖音/小红书热度时必须调用。"
            "会聚合B站/头条/Bing/百度等多源并按热度排序，返回摘要供你口头回答。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词，尽量具体"},
                "limit": {"type": "integer", "description": "返回条数，默认6，最大10"},
            },
            "required": ["query"],
        },
    },
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
}

JUNK_HOST = (
    "github.com/scenesfe",
    "github.com/pinappel",
    "github.com/vov6fg",
    "githubusercontent.com",
    "4g.cxhdu",
    "4g.uxtnzl",
    "guangming2026",
)
JUNK_TITLE = ("内容反馈", "您遇到了什么问题", "登录", "验证码", "Just a moment")
JUNK_SNIP = (
    "文章来源：http://www.4g",
    "at main · ",
    ".md at main",
)


def _clean(text: str) -> str:
    text = unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = text.replace("&ensp;", " ").replace("&#0183;", "·")
    return re.sub(r"\s+", " ", text).strip()


def _is_junk(title: str, snippet: str, url: str) -> bool:
    blob = f"{title} {snippet} {url}".lower()
    if "github.com" in blob and (".md" in blob or "at main" in blob):
        return True
    if any(j.lower() in blob for j in JUNK_HOST):
        return True
    if any(j in (title or "") for j in JUNK_TITLE):
        return True
    if any(j.lower() in blob for j in JUNK_SNIP):
        return True
    if len(_clean(title)) < 4 and len(_clean(snippet)) < 12:
        return True
    return False


def _heat_score(play: int = 0, like: int = 0, extra: float = 0.0) -> float:
    # log heat, avoid zero
    return math.log10(max(play, 0) + 10) * 2.0 + math.log10(max(like, 0) + 10) + extra


def _relevance(query: str, title: str, snippet: str) -> float:
    q = re.sub(r"\s+", "", query or "").lower()
    t = re.sub(r"\s+", "", f"{title}{snippet}").lower()
    if not q or not t:
        return 0.0
    score = 0.0
    # char ngrams / substring hits for Chinese
    if q in t:
        score += 8.0
    hit = 0
    # split rough tokens
    parts = [p for p in re.split(r"[，。！？、\s]+", query) if len(p) >= 2]
    for p in parts:
        if p.lower() in t:
            hit += 1
            score += 2.5
    # consecutive bigrams
    big = 0
    for i in range(max(0, len(q) - 1)):
        if q[i : i + 2] in t:
            big += 1
    score += min(big, 8) * 0.6
    if hit == 0 and q[:2] not in t:
        score -= 3.0
    return score


def _mk(
    title: str,
    snippet: str,
    url: str,
    source: str,
    play: int = 0,
    like: int = 0,
    extra_heat: float = 0.0,
    query: str = "",
) -> Dict:
    title = _clean(title)
    snippet = _clean(snippet)
    url = (url or "").strip()
    if _is_junk(title, snippet, url):
        return {}
    heat = _heat_score(play, like, extra_heat)
    rel = _relevance(query, title, snippet)
    # prefer items with actual explanation snippets
    snip_bonus = 3.0 if len(snippet) >= 40 else (1.0 if len(snippet) >= 16 else 0.0)
    src_bonus = {
        "B站": 4.0,
        "头条": 3.5,
        "Bing": 3.0,
        "百度": 2.0,
        "百度百科": 2.5,
        "抖音": 1.5,
        "小红书": 1.5,
        "UAPI": -1.0,
        "加速网关": 0.5,
    }.get(source, 0.0)
    score = heat + rel + snip_bonus + src_bonus
    return {
        "title": title,
        "snippet": snippet[:260],
        "url": url,
        "source": source,
        "play": play,
        "like": like,
        "score": score,
    }


def _search_bilibili(query: str, limit: int) -> List[Dict]:
    out: List[Dict] = []
    try:
        r = requests.get(
            "https://api.bilibili.com/x/web-interface/search/type",
            params={
                "search_type": "video",
                "keyword": query,
                "page": 1,
                "pagesize": max(limit, 10),
                "order": "totalrank",
            },
            headers={**HEADERS, "Referer": "https://search.bilibili.com"},
            timeout=12,
        )
        if not r.ok or not (r.text or "").lstrip().startswith("{"):
            # fallback all/v2
            r = requests.get(
                "https://api.bilibili.com/x/web-interface/search/all/v2",
                params={"keyword": query},
                headers={**HEADERS, "Referer": "https://search.bilibili.com"},
                timeout=12,
            )
        if not r.ok or not (r.text or "").lstrip().startswith("{"):
            return out
        data = r.json()
        if data.get("code") not in (0, None) and data.get("code") != 0:
            # still try parse
            pass
        items = []
        if isinstance((data.get("data") or {}).get("result"), list):
            # type search
            first = data["data"]["result"]
            if first and isinstance(first[0], dict) and "title" in first[0]:
                items = first
            else:
                for block in first:
                    if block.get("result_type") == "video":
                        items.extend(block.get("data") or [])
        for it in items:
            title = it.get("title") or ""
            desc = it.get("description") or it.get("desc") or ""
            url = (it.get("arcurl") or "").strip()
            if not url and it.get("bvid"):
                url = f"https://www.bilibili.com/video/{it.get('bvid')}"
            play = int(it.get("play") or it.get("view") or 0)
            like = int(it.get("like") or 0)
            author = _clean(it.get("author") or "")
            snip = _clean(desc)
            if author:
                snip = (f"UP主{author}。" + snip) if snip else f"UP主{author}，播放{play}"
            if play and "播放" not in snip:
                snip = (snip + f"。播放量{play}") if snip else f"播放量{play}"
            item = _mk(title, snip, url, "B站", play=play, like=like, query=query)
            if item:
                out.append(item)
            if len(out) >= limit * 2:
                break
    except Exception:
        pass
    return out


def _search_toutiao(query: str, limit: int) -> List[Dict]:
    out: List[Dict] = []
    try:
        r = requests.get(
            "https://so.toutiao.com/search",
            params={"keyword": query, "pd": "synthesis", "source": "input"},
            headers=HEADERS,
            timeout=12,
        )
        if not r.ok:
            return out
        html = r.text
        abstracts = re.findall(r'"abstract"\s*:\s*"((?:\\.|[^"\\]){8,280})"', html)
        titles = re.findall(r'"title"\s*:\s*"((?:\\.|[^"\\]){4,160})"', html)
        urls = re.findall(r'"open_url"\s*:\s*"((?:\\.|[^"\\])*)"', html)
        # pair by index loosely
        seen = set()
        n = max(len(abstracts), min(len(titles), limit * 3))
        for i in range(min(n, limit * 3)):
            title = ""
            snip = ""
            url = ""
            if i < len(titles):
                try:
                    title = json.loads(f'"{titles[i]}"')
                except Exception:
                    title = titles[i]
            if i < len(abstracts):
                try:
                    snip = json.loads(f'"{abstracts[i]}"')
                except Exception:
                    snip = abstracts[i]
            if i < len(urls):
                try:
                    url = json.loads(f'"{urls[i]}"')
                except Exception:
                    url = urls[i]
            title = _clean(title)
            snip = _clean(snip)
            if not title and not snip:
                continue
            key = title or snip[:40]
            if key in seen:
                continue
            seen.add(key)
            # toutiao abstracts are high quality for memes — boost heat
            item = _mk(
                title or snip[:30],
                snip or title,
                url,
                "头条",
                extra_heat=4.5,
                query=query,
            )
            if item:
                out.append(item)
            if len(out) >= limit * 2:
                break
    except Exception:
        pass
    return out


def _search_bing(query: str, limit: int) -> List[Dict]:
    out: List[Dict] = []
    try:
        r = requests.get(
            "https://cn.bing.com/search",
            params={"q": query, "setlang": "zh-CN", "mkt": "zh-CN"},
            headers=HEADERS,
            timeout=12,
        )
        if not r.ok:
            return out
        html = r.text
        blocks = re.findall(r'<li class="b_algo"[^>]*>(.*?)</li>', html, re.S | re.I)
        for i, b in enumerate(blocks):
            if len(out) >= limit * 2:
                break
            m_a = re.search(
                r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                b,
                re.S | re.I,
            )
            if not m_a:
                continue
            sn = re.search(r"<p[^>]*>(.*?)</p>", b, re.S | re.I)
            title = _clean(m_a.group(2))
            snippet = _clean(sn.group(1) if sn else "")
            item = _mk(
                title,
                snippet,
                m_a.group(1),
                "Bing",
                extra_heat=3.5 - i * 0.15,
                query=query,
            )
            if item:
                out.append(item)
    except Exception:
        pass
    return out


def _search_baidu(query: str, limit: int) -> List[Dict]:
    out: List[Dict] = []
    # 1) baike lite
    try:
        r = requests.get(
            "https://baike.baidu.com/api/searchlite",
            params={"word": query, "callback": ""},
            headers=HEADERS,
            timeout=8,
        )
        if r.ok:
            text = r.text.strip()
            if text.startswith("{"):
                data = r.json()
            else:
                m = re.search(r"\{.*\}", text, re.S)
                data = json.loads(m.group(0)) if m else {}
            for it in (data.get("list") or data.get("result") or [])[:limit]:
                title = it.get("title") or it.get("lemmaTitle") or ""
                snip = it.get("summary") or it.get("abstract") or it.get("desc") or ""
                url = it.get("url") or ""
                item = _mk(title, snip, url, "百度百科", extra_heat=3.0, query=query)
                if item:
                    out.append(item)
    except Exception:
        pass

    # 2) mobile baidu html JSON islands
    try:
        r = requests.get(
            "https://m.baidu.com/s",
            params={"word": query},
            headers=HEADERS,
            timeout=10,
        )
        if r.ok:
            # content / abstract fields often embedded
            for pat_title, pat_abs in (
                (r'"title"\s*:\s*"((?:\\.|[^"\\]){4,120})"', r'"abstract"\s*:\s*"((?:\\.|[^"\\]){8,220})"'),
                (r'"title"\s*:\s*"((?:\\.|[^"\\]){4,120})"', r'"description"\s*:\s*"((?:\\.|[^"\\]){8,220})"'),
            ):
                titles = re.findall(pat_title, r.text)
                abstracts = re.findall(pat_abs, r.text)
                for i in range(min(len(titles), max(len(abstracts), 1), limit * 2)):
                    try:
                        title = json.loads(f'"{titles[i]}"')
                    except Exception:
                        title = titles[i]
                    snip = ""
                    if i < len(abstracts):
                        try:
                            snip = json.loads(f'"{abstracts[i]}"')
                        except Exception:
                            snip = abstracts[i]
                    item = _mk(title, snip, "", "百度", extra_heat=2.2, query=query)
                    if item and "内容反馈" not in item["title"]:
                        out.append(item)
                if out:
                    break
    except Exception:
        pass

    # 3) suggestion as weak signal
    if len(out) < 2:
        try:
            r = requests.get(
                "https://suggestion.baidu.com/su",
                params={"wd": query, "cb": "window.baidu.sug"},
                headers=HEADERS,
                timeout=6,
            )
            m = re.search(r"s:\[(.*?)\]", r.text or "")
            if m:
                items = re.findall(r'"((?:\\.|[^"\\])*)"', m.group(1))
                for raw in items[:limit]:
                    try:
                        text = json.loads(f'"{raw}"')
                    except Exception:
                        text = _clean(raw)
                    item = _mk(text, f"百度相关搜索：{text}", "", "百度相关", extra_heat=0.5, query=query)
                    if item:
                        out.append(item)
        except Exception:
            pass
    return out


def _search_douyin_suggest(query: str, limit: int) -> List[Dict]:
    out: List[Dict] = []
    try:
        # Douyin web suggest (no login)
        r = requests.get(
            "https://www.douyin.com/aweme/v1/web/general/search/single/",
            params={"keyword": query, "search_channel": "aweme_general", "count": limit},
            headers={
                **HEADERS,
                "Referer": "https://www.douyin.com/search/" + quote(query),
            },
            timeout=10,
        )
        if r.ok and (r.text or "").lstrip().startswith("{"):
            data = r.json()
            for it in (data.get("data") or [])[: limit * 2]:
                aweme = it.get("aweme_info") or it.get("aweme") or it
                desc = _clean(aweme.get("desc") or aweme.get("title") or "")
                stats = aweme.get("statistics") or {}
                play = int(stats.get("play_count") or stats.get("digg_count") or 0)
                like = int(stats.get("digg_count") or 0)
                if not desc:
                    continue
                item = _mk(desc[:80], desc, "", "抖音", play=play, like=like, query=query)
                if item:
                    out.append(item)
    except Exception:
        pass

    # fallback: douyin hotboard via UAPI as related context when query looks like 热点
    if not out:
        try:
            r = requests.get(
                "https://uapis.cn/api/v1/misc/hotboard",
                params={"type": "douyin"},
                headers={**HEADERS, "Accept": "application/json"},
                timeout=8,
            )
            if r.ok:
                data = r.json()
                items = data.get("list") or data.get("data") or data.get("results") or []
                for it in items[:limit]:
                    if isinstance(it, dict):
                        title = it.get("title") or it.get("name") or it.get("word") or ""
                        hot = int(it.get("hot_value") or it.get("hot") or it.get("heat") or 0)
                    else:
                        title, hot = str(it), 0
                    # only keep if relevant to query
                    if _relevance(query, title, "") < 3.0:
                        continue
                    item = _mk(title, f"抖音热榜相关：{title}", "", "抖音热榜", play=hot, query=query)
                    if item:
                        out.append(item)
        except Exception:
            pass
    return out


def _search_xiaohongshu(query: str, limit: int) -> List[Dict]:
    out: List[Dict] = []
    try:
        # public search page often needs login; try extract __INITIAL_STATE__ if present
        r = requests.get(
            "https://www.xiaohongshu.com/search_result",
            params={"keyword": query, "source": "web_search_result_notes"},
            headers={
                **HEADERS,
                "Referer": "https://www.xiaohongshu.com/",
            },
            timeout=10,
        )
        if not r.ok:
            return out
        html = r.text
        # note titles in window.__INITIAL_STATE__
        titles = re.findall(
            r'"display_title"\s*:\s*"((?:\\.|[^"\\]){4,120})"',
            html,
        )
        descs = re.findall(
            r'"desc"\s*:\s*"((?:\\.|[^"\\]){4,200})"',
            html,
        )
        liked = re.findall(r'"liked_count"\s*:\s*"?(\d+)"?', html)
        for i, raw in enumerate(titles[: limit * 2]):
            try:
                title = json.loads(f'"{raw}"')
            except Exception:
                title = _clean(raw)
            snip = ""
            if i < len(descs):
                try:
                    snip = json.loads(f'"{descs[i]}"')
                except Exception:
                    snip = _clean(descs[i])
            like = int(liked[i]) if i < len(liked) else 0
            item = _mk(title, snip or title, "", "小红书", like=like, extra_heat=1.5, query=query)
            if item:
                out.append(item)
    except Exception:
        pass

    # UAPI hotboard xhs/xiaohongshu if available
    if not out:
        for src in ("xiaohongshu", "xhs", "red"):
            try:
                r = requests.get(
                    "https://uapis.cn/api/v1/misc/hotboard",
                    params={"type": src},
                    headers={**HEADERS, "Accept": "application/json"},
                    timeout=6,
                )
                if not r.ok:
                    continue
                data = r.json()
                items = data.get("list") or data.get("data") or []
                for it in items[:limit]:
                    title = (it.get("title") if isinstance(it, dict) else str(it)) or ""
                    if _relevance(query, title, "") < 3.0:
                        continue
                    hot = int((it.get("hot_value") if isinstance(it, dict) else 0) or 0)
                    item = _mk(title, f"小红书热榜相关：{title}", "", "小红书热榜", play=hot, query=query)
                    if item:
                        out.append(item)
                if out:
                    break
            except Exception:
                continue
    return out


def _search_uapi(query: str, limit: int) -> List[Dict]:
    out: List[Dict] = []
    try:
        r = requests.post(
            "https://uapis.cn/api/v1/search/aggregate",
            json={"query": query, "limit": max(limit, 8)},
            headers={**HEADERS, "Content-Type": "application/json", "Accept": "application/json"},
            timeout=16,
        )
        if not r.ok:
            return out
        for it in (r.json().get("results") or []):
            title = it.get("title") or ""
            snip = it.get("snippet") or ""
            url = it.get("url") or ""
            # demote github junk heavily via _is_junk
            item = _mk(title, snip, url, "UAPI", extra_heat=1.0, query=query)
            if item:
                # extra filter: require some relevance
                if item["score"] < 4.0:
                    continue
                out.append(item)
    except Exception:
        pass
    return out


def _search_cpp_gateway(query: str, limit: int) -> List[Dict]:
    out: List[Dict] = []
    for url in (
        "http://172.17.0.1:18081/search",
        "http://host.docker.internal:18081/search",
        "http://127.0.0.1:18081/search",
    ):
        try:
            r = requests.get(url, params={"q": query, "limit": limit}, timeout=4)
            if r.status_code != 200:
                continue
            body = r.json()
            for it in body.get("results") or body.get("data") or []:
                item = _mk(
                    it.get("title") or "",
                    it.get("snippet") or it.get("body") or "",
                    it.get("url") or it.get("href") or "",
                    "加速网关",
                    extra_heat=1.0,
                    query=query,
                )
                if item:
                    out.append(item)
            if out:
                return out
        except Exception:
            continue
    return out


def _dedupe_rank(items: List[Dict], limit: int) -> List[Dict]:
    seen = set()
    ranked = sorted(items, key=lambda x: float(x.get("score") or 0), reverse=True)
    out: List[Dict] = []
    for it in ranked:
        title = (it.get("title") or "").strip()
        key = re.sub(r"\s+", "", title)[:40]
        if not key or key in seen:
            continue
        # near-dup by shared prefix
        if any(key[:16] == s[:16] for s in seen if len(s) >= 16):
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= limit:
            break
    return out


@register_function("web_search", WEB_SEARCH_FUNCTION_DESC, ToolType.SYSTEM_CTL)
def web_search(conn, query: str, limit: int = 6):
    query = (query or "").strip()
    if not query:
        return ActionResponse(Action.REQLLM, "搜索词为空，请告诉我要查什么", None)
    try:
        limit = max(1, min(int(limit or 6), 10))
    except Exception:
        limit = 6

    providers = [
        ("B站", _search_bilibili),
        ("头条", _search_toutiao),
        ("Bing", _search_bing),
        ("百度", _search_baidu),
        ("抖音", _search_douyin_suggest),
        ("小红书", _search_xiaohongshu),
        ("UAPI", _search_uapi),
        ("加速网关", _search_cpp_gateway),
    ]

    pooled: List[Dict] = []
    source_hits: List[str] = []

    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(fn, query, limit): name for name, fn in providers}
        for fut in as_completed(futs, timeout=20):
            name = futs[fut]
            try:
                got = fut.result() or []
            except Exception as e:
                try:
                    conn.logger.bind(tag=TAG).warning(f"web_search {name} failed: {e}")
                except Exception:
                    pass
                got = []
            if got:
                source_hits.append(f"{name}+{len(got)}")
                pooled.extend(got)

    results = _dedupe_rank(pooled, limit)
    source = ",".join(source_hits) if source_hits else "无"

    try:
        conn.logger.bind(tag=TAG).info(
            f"web_search q={query!r} n={len(results)} via={source} "
            f"top={[(r.get('source'), round(r.get('score',0),1)) for r in results[:3]]}"
        )
    except Exception:
        pass

    if not results:
        return ActionResponse(
            Action.REQLLM,
            (
                f"联网搜索「{query}」暂时没有拿到可用结果。"
                f"请诚实告诉用户现在搜不到，可建议换关键词，不要编造。"
            ),
            None,
        )

    lines = [
        f"联网搜索「{query}」多平台热度结果（来源汇总:{source}）：",
        "【硬性要求】你必须依据下列摘要回答；优先引用播放量/热度高、摘要完整的条目；"
        "解释清楚梗/事件是什么、怎么火的；禁止说“无法提供/找不到确切来源”；禁止念链接；口语短句。",
    ]
    for i, it in enumerate(results, 1):
        heat = ""
        if it.get("play"):
            heat = f" 热度播放≈{it['play']}"
        elif it.get("like"):
            heat = f" 点赞≈{it['like']}"
        lines.append(f"{i}. [{it.get('source')}] {it.get('title') or '结果'}{heat}")
        if it.get("snippet"):
            lines.append(f"   摘要：{it['snippet'][:220]}")
    return ActionResponse(Action.REQLLM, "\n".join(lines), None)
