"""TMDb 榜单取源：四个榜单 id → 统一条目形状。

只走标准库（`urllib.request`），HTTP 一律经**可注入的 opener**：生产用
`default_opener`（`urllib.request.urlopen` 的薄包装），离线测试注入假 opener，
于是缓存与路由这两层也能在不触网的前提下被完整驱动（A2/A3/A4）。

本模块**不抛异常**：HTTP 错误 / 网络错误 / 坏 JSON / 意外载荷都折成结构化结果
（`status` + `error` + 空 `items`），由调用方决定降级方式 —— 热门入口要的是
「stale 榜单」而不是 500。

`api_key` 为空时直接返回失败、不发请求：密钥缺失是调用方的决定（路由在无 key
时根本不会走到这里），取源层不该替它发一次注定 401 的请求。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

TMDB_API_BASE = "https://api.themoviedb.org/3"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) JellyfinTrending/1.0"
DEFAULT_LANGUAGE = "zh-CN"
DEFAULT_PAGE = 1
DEFAULT_TIMEOUT = 15

# 榜单 id → 端点与展示元数据。`date_field` 是年份的来源字段（电影与剧集不同名），
# `kind` 是统一条目里的类型取值（"movie" | "series"）。
LISTS = {
    "trending_movie_week": {
        "endpoint": "/trending/movie/week",
        "label": "本周热门电影",
        "kind": "movie",
        "date_field": "release_date",
    },
    "trending_tv_week": {
        "endpoint": "/trending/tv/week",
        "label": "本周热门剧集",
        "kind": "series",
        "date_field": "first_air_date",
    },
    "popular_movie": {
        "endpoint": "/movie/popular",
        "label": "流行电影",
        "kind": "movie",
        "date_field": "release_date",
    },
    "popular_tv": {
        "endpoint": "/tv/popular",
        "label": "流行剧集",
        "kind": "series",
        "date_field": "first_air_date",
    },
}

# 缺省的榜单顺序 = 路由「不传 lists 就跑全部四个」的取值顺序。
LIST_ORDER = tuple(LISTS)

_HTTP_ERROR_BODY_CHARS = 200


def default_opener(request, timeout=DEFAULT_TIMEOUT):
    """默认 opener：`urllib.request.urlopen` 的薄包装，也是测试注入替身的那条缝。"""
    return urllib.request.urlopen(request, timeout=timeout)


def build_url(list_id, api_key, language=DEFAULT_LANGUAGE, page=DEFAULT_PAGE):
    """拼端点 URL。TMDb v3 的密钥走 query（官方约定），不做 header 变体。"""
    spec = LISTS[list_id]
    params = {"api_key": api_key, "language": language, "page": int(page)}
    return f"{TMDB_API_BASE}{spec['endpoint']}?{urllib.parse.urlencode(params)}"


def _coerce_int(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _coerce_float(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _year_from(date_text):
    """日期串前 4 位是整数才是年份；缺失或不成形一律 null，不编年份。"""
    head = str(date_text or "").strip()[:4]
    if len(head) == 4 and head.isdigit():
        return int(head)
    return None


def normalize_item(record, list_id):
    """一条 TMDb 记录 → 统一条目；不是字典的记录丢掉（返回 None）。

    电影用 `title`/`release_date`，剧集用 `name`/`first_air_date`；`kind` 由榜单
    决定而不是由记录里的字段猜，所以同一部作品出现在电影榜和剧集榜时类型跟着榜单走。
    """
    if not isinstance(record, dict):
        return None
    spec = LISTS[list_id]
    poster = record.get("poster_path")
    return {
        "tmdb_id": _coerce_int(record.get("id")),
        "title": str(record.get("title") or record.get("name") or ""),
        "original_title": str(
            record.get("original_title") or record.get("original_name") or ""
        ),
        "year": _year_from(record.get(spec["date_field"])),
        "kind": spec["kind"],
        "poster_path": str(poster) if poster else None,
        "vote_average": _coerce_float(record.get("vote_average")),
    }


def _failure(list_id, status, error):
    spec = LISTS.get(list_id) or {}
    return {
        "id": list_id,
        "label": spec.get("label") or list_id,
        "kind": spec.get("kind") or "",
        "status": int(status or 0),
        "error": str(error or ""),
        "items": [],
    }


def _http_error_body(exc):
    """读错误响应体（前 200 字），读完就关 —— 失败的那次响应也是要释放的资源。"""
    try:
        return exc.read().decode("utf-8", "replace")[:_HTTP_ERROR_BODY_CHARS]
    except (OSError, AttributeError):
        return ""
    finally:
        close = getattr(exc, "close", None)
        if callable(close):
            try:
                close()
            except OSError:
                pass


def fetch_list(
    list_id,
    api_key,
    language=DEFAULT_LANGUAGE,
    page=DEFAULT_PAGE,
    opener=None,
    timeout=DEFAULT_TIMEOUT,
):
    """取一个榜单的一页，永远返回结构化结果：

        {"id", "label", "kind", "status", "error", "items"}

    `status == 200` 且 `error == ""` 才算取到；其余情况 `items` 为空列表，
    调用方（缓存层）据此决定回退还是重试。`opener` 缺省用 `default_opener`。
    """
    if list_id not in LISTS:
        return _failure(list_id, 0, f"unknown list id: {list_id}")
    if not str(api_key or "").strip():
        return _failure(list_id, 0, "api_key is required")

    url = build_url(list_id, api_key, language=language, page=page)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        response = (opener or default_opener)(request, timeout=timeout)
    except urllib.error.HTTPError as exc:  # OSError 的子类，必须排在前面
        status = getattr(exc, "code", 0) or 0
        body = _http_error_body(exc)
        return _failure(list_id, status, f"HTTP {status}: {body}".strip())
    except (OSError, ValueError) as exc:
        return _failure(list_id, 0, f"network error: {exc}")

    try:
        raw = response.read()
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except OSError:
                pass

    status = getattr(response, "status", 200)
    if status != 200:
        return _failure(list_id, status, f"HTTP {status}")

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return _failure(list_id, 0, f"invalid json: {exc}")
    if not isinstance(payload, dict):
        return _failure(list_id, 0, "unexpected payload shape")
    records = payload.get("results")
    if not isinstance(records, list):
        return _failure(list_id, 0, "unexpected payload shape")

    items = [
        item
        for item in (normalize_item(record, list_id) for record in records)
        if item is not None
    ]
    return {
        "id": list_id,
        "label": LISTS[list_id]["label"],
        "kind": LISTS[list_id]["kind"],
        "status": 200,
        "error": "",
        "items": items,
    }


def fetch_lists(
    list_ids,
    api_key,
    language=DEFAULT_LANGUAGE,
    page=DEFAULT_PAGE,
    opener=None,
    timeout=DEFAULT_TIMEOUT,
):
    """按给定顺序逐个取（串行：四个榜单、每个一次请求，不值得并发）。"""
    return [
        fetch_list(
            list_id,
            api_key,
            language=language,
            page=page,
            opener=opener,
            timeout=timeout,
        )
        for list_id in list_ids
    ]
