"""JuiceMusic API client for xiaozhi online play_music."""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests

DEFAULT_BASE = "https://music.anjuice.lol"
DEFAULT_KEY = os.environ.get("JUICE_API_KEY", "")
DEFAULT_SOURCE = "netease"
ALLOWED_SOURCES = ("netease", "bilibili", "kugou", "qq", "migu", "kuwo")


def normalize_source(raw: Optional[str], default: str = DEFAULT_SOURCE) -> str:
    s = (raw or "").strip().lower()
    if not s:
        return default
    aliases = {
        "netease": "netease",
        "网易云": "netease",
        "网易": "netease",
        "网易云音乐": "netease",
        "cloudmusic": "netease",
        "bilibili": "bilibili",
        "b站": "bilibili",
        "哔哩哔哩": "bilibili",
        "哔哩": "bilibili",
        "bili": "bilibili",
        "kugou": "kugou",
        "酷狗": "kugou",
        "qq": "qq",
        "qq音乐": "qq",
        "q音": "qq",
        "migu": "migu",
        "咪咕": "migu",
        "kuwo": "kuwo",
        "酷我": "kuwo",
    }
    if s in aliases:
        return aliases[s]
    for k, v in aliases.items():
        if k in s:
            return v
    return s if s in ALLOWED_SOURCES else default


def parse_source_from_text(text: str) -> Tuple[Optional[str], str]:
    """Extract music source from user text; return (source_or_None, cleaned_text)."""
    raw = text or ""
    patterns = [
        (r"(?:用|使用|切换到|换成)?\s*(网易云音乐|网易云|网易)", "netease"),
        (r"(?:用|使用|切换到|换成)?\s*(B站|哔哩哔哩|哔哩|bilibili|Bilibili)", "bilibili"),
        (r"(?:用|使用)?\s*(酷狗|kugou)", "kugou"),
        (r"(?:用|使用)?\s*(QQ音乐|qq音乐|Q音)", "qq"),
        (r"(?:用|使用)?\s*(咪咕|migu)", "migu"),
        (r"(?:用|使用)?\s*(酷我|kuwo)", "kuwo"),
        (r"\bsource\s*[:=]\s*(netease|bilibili|kugou|qq|migu|kuwo)\b", None),
    ]
    found = None
    cleaned = raw
    for pat, fixed in patterns:
        m = re.search(pat, cleaned, flags=re.I)
        if m:
            found = fixed or m.group(1).lower()
            cleaned = (cleaned[: m.start()] + " " + cleaned[m.end() :]).strip()
            cleaned = re.sub(r"\s+", " ", cleaned)
            break
    return (normalize_source(found) if found else None), cleaned


def source_label(source: str) -> str:
    return {
        "netease": "网易云",
        "bilibili": "B站",
        "kugou": "酷狗",
        "qq": "QQ音乐",
        "migu": "咪咕",
        "kuwo": "酷我",
    }.get(source, source)


def _headers(api_key: str) -> Dict[str, str]:
    return {
        "X-Api-Key": api_key,
        "User-Agent": "xiaozhi-esp32-server-music/juice",
        "Accept": "*/*",
    }


def search_tracks(
    base: str,
    api_key: str,
    keyword: str,
    source: str = DEFAULT_SOURCE,
    limit: int = 8,
    timeout: int = 15,
) -> List[Dict[str, Any]]:
    """Return track list (may be empty)."""
    source = normalize_source(source)
    url = base.rstrip("/") + "/api/v1/search"
    r = requests.get(
        url,
        params={"q": keyword, "source": source, "limit": limit},
        headers=_headers(api_key),
        timeout=timeout,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("status") != "success":
        raise RuntimeError(body.get("message") or "JuiceMusic search failed")
    tracks = list((body.get("data") or {}).get("tracks") or [])
    for t in tracks:
        t["_juice_source"] = source
    return tracks


def search_tracks_multi(
    base: str,
    api_key: str,
    keyword: str,
    sources: Optional[List[str]] = None,
    limit: int = 8,
    timeout: int = 15,
) -> Tuple[List[Dict[str, Any]], str]:
    """
    Search preferred sources in order.
    Default: netease then bilibili (unless user forced a source).
    Returns (tracks, used_source).
    """
    order = sources or [DEFAULT_SOURCE, "bilibili"]
    last_err = None
    for src in order:
        try:
            tracks = search_tracks(base, api_key, keyword, source=src, limit=limit, timeout=timeout)
            if tracks:
                return tracks, normalize_source(src)
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    return [], order[0] if order else DEFAULT_SOURCE


def search_track(
    base: str,
    api_key: str,
    keyword: str,
    source: str = DEFAULT_SOURCE,
    limit: int = 5,
    timeout: int = 15,
) -> Optional[Dict[str, Any]]:
    tracks = search_tracks(base, api_key, keyword, source=source, limit=limit, timeout=timeout)
    return tracks[0] if tracks else None


def track_label(track: Dict[str, Any]) -> str:
    title = (track.get("title") or track.get("name") or "未知歌曲").strip()
    artist = (track.get("author") or track.get("singer") or track.get("artist") or "").strip()
    return f"{title} - {artist}".strip(" -") if artist else title


def track_id_of(track: Dict[str, Any]) -> Optional[str]:
    tid = track.get("id") or track.get("bvid")
    return str(tid) if tid is not None else None


def download_stream(
    base: str,
    api_key: str,
    track_id: str,
    dest_path: str,
    timeout: int = 60,
) -> Tuple[str, int]:
    url = base.rstrip("/") + f"/api/v1/audio/{track_id}/stream"
    with requests.get(
        url,
        headers=_headers(api_key),
        stream=True,
        timeout=timeout,
        allow_redirects=True,
    ) as r:
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        written = 0
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                f.write(chunk)
                written += len(chunk)
        if written < 1024:
            raise RuntimeError(f"JuiceMusic stream too small: {written} bytes")
        return ctype, written


def fetch_lyric_lrc(
    base: str,
    api_key: str,
    track_id: str,
    timeout: int = 15,
) -> str:
    url = base.rstrip("/") + f"/api/v1/lyric/{track_id}"
    r = requests.get(url, headers=_headers(api_key), timeout=timeout)
    if r.status_code != 200:
        return ""
    body = r.json()
    if body.get("status") != "success":
        return ""
    data = body.get("data") or {}
    lrc = data.get("lrc") or ""
    if lrc:
        return lrc.replace("\\n", "\n")
    lines = data.get("lines") or []
    out = []
    for item in lines:
        ms = int(item.get("time_ms") or 0)
        text = (item.get("text") or "").strip()
        if not text:
            continue
        m, s = divmod(ms // 1000, 60)
        cs = (ms % 1000) // 10
        out.append(f"[{m:02d}:{s:02d}.{cs:02d}]{text}")
    return "\n".join(out)


def safe_filename(title: str, artist: str = "") -> str:
    name = f"{title} - {artist}".strip(" -") if artist else title
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip() or "unknown"
    return name[:120]


def ext_from_content_type(ctype: str, head: bytes = b"") -> str:
    ctype = (ctype or "").lower()
    if "mpeg" in ctype or "mp3" in ctype:
        return "mp3"
    if "mp4" in ctype or "m4a" in ctype or "aac" in ctype:
        return "m4a"
    if "wav" in ctype:
        return "wav"
    if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3"
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return "m4a"
    return "mp3"


def resolve_config(music_config: Optional[dict] = None) -> Dict[str, str]:
    cfg = music_config or {}
    base = (
        cfg.get("juice_base")
        or os.environ.get("JUICE_BASE")
        or DEFAULT_BASE
    ).rstrip("/")
    key = (
        cfg.get("juice_api_key")
        or os.environ.get("JUICE_API_KEY")
        or DEFAULT_KEY
    )
    source = normalize_source(
        cfg.get("juice_source") or os.environ.get("JUICE_SOURCE") or DEFAULT_SOURCE
    )
    return {"base": base, "api_key": key, "source": source}


def build_search_keyword(song_name: Optional[str], artist: Optional[str] = None) -> str:
    song = (song_name or "").strip()
    art = (artist or "").strip()
    if art and song:
        return f"{art} {song}"
    if art:
        return art
    return song or "晴天"


def pick_track(
    tracks: List[Dict[str, Any]],
    song_name: Optional[str] = None,
    artist: Optional[str] = None,
    choose: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Only pick by explicit choose index. Never auto-pick by name/single result."""
    if not tracks or choose is None:
        return None
    try:
        idx = int(choose)
    except Exception:
        return None
    if 1 <= idx <= len(tracks):
        return tracks[idx - 1]
    return None


_COVER_MARKERS = (
    "原唱",
    "翻唱",
    "cover",
    "钢琴",
    "古筝",
    "小提琴",
    "dj",
    "伴奏",
    "纯音乐",
    "铃声",
    "直播",
    "踩点",
    "人声分离",
    "女声版",
    "男声版",
    "合唱版",
    "深情版",
)


def _artist_aliases(artist: str) -> List[str]:
    a = (artist or "").strip().lower()
    if not a:
        return []
    aliases = {a, a.replace(" ", "")}
    table = {
        "周杰伦": ["周杰伦", "jay", "jay chou", "周董"],
        "jay": ["周杰伦", "jay", "jay chou"],
        "jay chou": ["周杰伦", "jay", "jay chou"],
        "邓紫棋": ["邓紫棋", "gem", "g.e.m"],
        "林俊杰": ["林俊杰", "jj", "jj lin"],
        "陈奕迅": ["陈奕迅", "eason", "eason chan"],
    }
    for k, vals in table.items():
        if k in a or a in k:
            aliases.update(v.lower() for v in vals)
    return list(aliases)


def rank_tracks(
    tracks: List[Dict[str, Any]],
    song_name: Optional[str] = None,
    artist: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Prefer original artist / exact title; demote covers and instrumental versions."""
    if not tracks:
        return []
    song = (song_name or "").strip().lower()
    artist_keys = _artist_aliases(artist or "")

    def score(t: Dict[str, Any]) -> float:
        title = (t.get("title") or t.get("name") or "").strip()
        art = (
            t.get("author") or t.get("singer") or t.get("artist") or t.get("artists") or ""
        )
        if isinstance(art, list):
            art = " ".join(str(x) for x in art)
        art = str(art).strip()
        title_l = title.lower()
        art_l = art.lower()
        s = 0.0
        # exact-ish title
        if song:
            pure = re.sub(r"[\(\[（].*?[\)\]）]", "", title_l).strip()
            if song == pure or song == title_l:
                s += 12
            elif song in title_l:
                s += 6
            else:
                s -= 2
        # artist match
        if artist_keys:
            art_compact = art_l.replace(" ", "")
            exact_artist = (artist or "").strip().lower()
            if exact_artist and (exact_artist == art_l or exact_artist == art_compact):
                s += 28
            elif any(k and k in art_l for k in artist_keys):
                s += 18
            elif any(k and k in title_l for k in artist_keys) and "原唱" in title:
                # "晴天 (原唱 周杰伦) - RyaVocal" — title mentions artist but singer is cover
                s -= 8
            else:
                s -= 10
        # cover / remix demotion
        blob = f"{title_l} {art_l}"
        for m in _COVER_MARKERS:
            if m in blob:
                s -= 6
        # slight prefer shorter clean titles
        if "(" not in title and "（" not in title:
            s += 1
        return s

    return sorted(tracks, key=score, reverse=True)
