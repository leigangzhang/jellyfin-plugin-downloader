#!/usr/bin/env python3
"""BT 资源站清单与 L1 关键字组合的唯一事实源。

这两个事实曾经硬编码在 `search_pool.py` 里（`DEFAULT_BASE_URL` /
`PUBLISHER_QUERY_TOKEN`）。现在它们由插件配置页写进环境变量，因此**必须只有一处**
定义「配置长什么样、默认值是什么、什么算不合法」——`search_pool.py`（取源）与
`console_server.py`（云盘通道要按站点取网页搜索地址）都从这里读，避免两边各有
一套解释。

配置形态
--------
* `JMD_BT_SITES`：JSON 数组，每项
  ``{"name": 显示名, "kind": 适配器, "base_url": 接口地址, "web_search_url": 网页搜索地址}``。
  ``kind`` 决定用哪段代码解析该站的响应，目前支持 ``diao``（结构化搜索 API）与
  ``torrents-csv``（带 seeder 快照的聚合索引）——**加一个站不用改代码，加一种协议
  才需要新适配器**。``web_search_url`` 可留空；非空时**必须含 `{q}` 占位符**，它
  同时是云盘通道的入口（把 `{q}` 换成片名后当资源页抓取）。
* `JMD_L1_KEYWORDS`：JSON 数组（也接受换行/逗号分隔的纯文本），每项是一条
  **查询后缀**，会与片名拼成一条查询，按顺序排在关键词阶梯的最前面。显式给一个
  空数组 = 不要这一档；不设或空串 = 用内置默认。

空 = 用内置默认值
-----------------
两个变量为空（或没设）时回落到这里的默认值：默认资源站就是当前在用的两个，默认
关键字组合是「高清剧集发布网 / DDHDTV / BBHDTV」。这保证老安装的 XML 里没有这两个
键时行为与改动前一致。

坏配置不静默
------------
解析或校验失败时**不回落默认值**：返回空清单 + 错误明细，由调用方决定怎么报。
悄悄用默认值会让「我配了但没生效」变成一个看不见的状态。
"""

import json
import re
import urllib.parse
from os import environ

ENV_SITES = "JMD_BT_SITES"
ENV_KEYWORDS = "JMD_L1_KEYWORDS"

DIAO_KIND = "diao"
TORRENTS_CSV_KIND = "torrents-csv"
# 已支持的适配器。kind 是「用哪段代码解析响应」的开关，不是站点名。
SITE_KINDS = (DIAO_KIND, TORRENTS_CSV_KIND)

DEFAULT_SITES = (
    {
        "name": "diao.im",
        "kind": DIAO_KIND,
        "base_url": "https://m.diao.im/api/search",
        "web_search_url": "",
    },
    {
        "name": "torrents-csv",
        "kind": TORRENTS_CSV_KIND,
        "base_url": "https://torrents-csv.com/service/search",
        "web_search_url": "",
    },
)

# 发布者署名档位的默认后缀：这三家发布网（DDHDTV / BPHDTV / BTHDTV）的种子名都写成
# `【高清剧集网发布 www.DDHDTV.com】…`，搜这两个词比搜裸片名精确得多。默认取「实测有效
# 的那两条」而不是「读起来像站点名的短语」——2026-10-13 在 m.diao.im 实测：
#   `漫长的季节 DDHDTV`      -> 6/6 命中该发布者
#   `漫长的季节 高清剧集网`   -> 10/10（覆盖 DDHDTV 与 BPHDTV 两条线）
#   `漫长的季节 BTHDTV`      -> 10/10
#   `漫长的季节 高清剧集发布网` -> 0 命中（名字里写的是「高清剧集网发布」，词序不同）
#   `漫长的季节 BBHDTV`      -> 0 命中（这个索引里没有该署名）
# 用户可以在插件配置里换成任意组合；每多一条就是每个片名多一次查询。
DEFAULT_ANCHOR_KEYWORDS = ("高清剧集网", "DDHDTV")

# 边界（P10：批量操作与外部输入都要有上限）。站点数决定每站分到的墙钟预算，
# 关键字组合数决定阶梯长度，两个都必须有限。
MAX_SITES = 8
MAX_KEYWORDS = 8
MAX_URL_LEN = 300
MAX_NAME_LEN = 40
MAX_KEYWORD_LEN = 64

_WHITESPACE_RE = re.compile(r"\s")
_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


def _clean_text(value, limit):
    """压掉空白并截断；非字符串一律当空。"""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _normalise_url(value):
    """返回 (url, error)。空串是合法的「不提供」。"""
    url = _clean_text(value, MAX_URL_LEN)
    if not url:
        return "", ""
    if not _SCHEME_RE.match(url) or _WHITESPACE_RE.search(url):
        return "", f"地址必须是 http(s) URL：{url!r}"
    return url, ""


def normalise_site(raw):
    """一条站点配置 → (site, error)。字段缺失按规则补，非法就整条拒绝。"""
    if not isinstance(raw, dict):
        return None, f"每个资源站必须是对象，收到 {type(raw).__name__}"
    kind = _clean_text(raw.get("kind"), MAX_NAME_LEN).lower()
    if kind not in SITE_KINDS:
        return None, f"未知的资源站类型 {kind!r}（支持 {'/'.join(SITE_KINDS)}）"
    base_url, error = _normalise_url(raw.get("base_url"))
    if error:
        return None, error
    if not base_url:
        return None, f"{kind} 站点缺少接口地址"
    web_search_url, error = _normalise_url(raw.get("web_search_url"))
    if error:
        return None, error
    if web_search_url and "{q}" not in web_search_url:
        return None, f"网页搜索地址必须包含 {{q}} 占位符：{web_search_url!r}"
    name = _clean_text(raw.get("name"), MAX_NAME_LEN) or kind
    return (
        {
            "name": name,
            "kind": kind,
            "base_url": base_url,
            "web_search_url": web_search_url,
        },
        "",
    )


def normalise_keyword(raw):
    """一条关键字组合 → (keyword, error)。空串按「忽略」处理。"""
    keyword = _clean_text(raw, MAX_KEYWORD_LEN)
    if not keyword:
        return None, ""
    return keyword, ""


def _load_json_array(text, env_name):
    """把环境变量里的一段文本解析成 JSON 数组；空串返回 (None, "") 交给调用方兜底。"""
    text = (text or "").strip()
    if not text:
        return None, ""
    try:
        payload = json.loads(text)
    except ValueError as exc:
        return None, f"{env_name} 不是合法 JSON：{exc}"
    if not isinstance(payload, list):
        return None, f"{env_name} 必须是数组，收到 {type(payload).__name__}"
    return payload, ""


def load_sites(text=None):
    """解析资源站清单，返回 (sites, errors)。

    `text` 为 None 时读环境变量。空配置 → 默认两个站（向后兼容）。解析/校验出错时
    **不回落默认值**：宁可让调用方报一条看得见的错误。
    """
    if text is None:
        text = environ.get(ENV_SITES, "")
    payload, error = _load_json_array(text, ENV_SITES)
    if error:
        return [], [error]
    if payload is None:
        return [dict(site) for site in DEFAULT_SITES], []

    sites = []
    errors = []
    if len(payload) > MAX_SITES:
        errors.append(f"资源站最多 {MAX_SITES} 个，多余的 {len(payload) - MAX_SITES} 个已忽略")
        payload = payload[:MAX_SITES]
    for index, raw in enumerate(payload, start=1):
        site, error = normalise_site(raw)
        if error:
            errors.append(f"第 {index} 个资源站：{error}")
            continue
        sites.append(site)
    if not sites:
        errors.append("没有一个可用的资源站")
    return sites, errors


def load_keywords(text=None):
    """解析 L1 关键字组合，返回 (keywords, errors)。

    环境变量既接受 JSON 数组，也接受换行/逗号分隔的纯文本（手改时更省事）。空配置
    → 默认组合（向后兼容）。
    """
    if text is None:
        text = environ.get(ENV_KEYWORDS, "")
    text = (text or "").strip()
    if not text:
        return list(DEFAULT_ANCHOR_KEYWORDS), []

    payload = None
    if text.startswith("["):
        payload, error = _load_json_array(text, ENV_KEYWORDS)
        if error:
            return [], [error]
    if payload is None:
        payload = re.split(r"[\n,，]+", text)
    if not payload:
        # 显式给空数组 = 「不跑锚点档」。站点与关键字不一样：没有站就没得搜（那是
        # 错误），没有关键字只是少了一档精度更高的查询。配置页发不出这个值——它把
        # 空文本当作「用默认」，所以这条只服务于手工传参。
        return [], []

    keywords = []
    errors = []
    if len(payload) > MAX_KEYWORDS:
        errors.append(f"关键字组合最多 {MAX_KEYWORDS} 条，多余的 {len(payload) - MAX_KEYWORDS} 条已忽略")
        payload = payload[:MAX_KEYWORDS]
    for index, raw in enumerate(payload, start=1):
        if not isinstance(raw, str):
            errors.append(f"第 {index} 条关键字不是字符串：{raw!r}")
            continue
        keyword, error = normalise_keyword(raw)
        if error:
            errors.append(f"第 {index} 条关键字：{error}")
            continue
        if keyword:
            keywords.append(keyword)
    if not keywords:
        errors.append("没有一条可用的关键字组合")
    return keywords, errors


def web_search_urls(title, sites):
    """把片名代进各站点的网页搜索模板，返回 [(站点名, url)]。

    只有填了 `web_search_url` 的站点会产生条目；一条都没有时返回空列表——调用方
    必须把「云盘通道没有入口」明说出来，而不是让它看起来像「搜了但没结果」。
    """
    query = urllib.parse.quote((title or "").strip())
    found = []
    for site in sites or []:
        if not isinstance(site, dict):
            continue
        template = site.get("web_search_url") or ""
        if not template or "{q}" not in template:
            continue
        name = site.get("name") or site.get("kind") or "site"
        found.append((name, template.replace("{q}", query)))
    return found
