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
  一个站至少要有一个入口：

  - ``base_url`` + ``kind``：磁力入口。``kind`` 决定用哪段代码解析该站的响应，目前支持
    ``diao``（结构化搜索 API）与 ``torrents-csv``（带 seeder 快照的聚合索引）——**加一个
    站不用改代码，加一种协议才需要新适配器**。
  - ``web_search_url``：云盘入口，**必须含 `{q}` 占位符**（把 `{q}` 换成片名后当资源页
    抓取，从中解析 pan.xunlei.com 分享链接）。它可以和磁力入口并存，**也可以单独存在**
    ——只发云盘资源、没有搜索 API 的站就靠它，此时 ``kind`` 无意义，会被归一化成空串。

配置页只让用户填一个地址
------------------------
插件配置页一个站只给一个输入框（完整站点地址），因此 ``classify_site_url`` 是「这个地址
该走哪个入口」的唯一判定处：配置页用它显示识别结果，``normalise_site`` 在 ``kind`` 缺省
时用它补类型。显式传 ``kind`` 仍然被接受（旧 XML、脚本调用），但地址本身判定不出入口时
不再默认当成磁力站，而是显式报错——见下面的「坏配置不静默」。
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

# 配置页只有一个地址输入框，站点类型必须能从地址本身判定出来。判定只看主机名与路径，
# 不看查询串——`?q={q}` 那种模板的 `{q}` 在 URL 解析后会被百分号编码，匹配不了。
_DIAO_HOSTS = ("m.diao.im", "diao.im")
_DIAO_SEARCH_PATH = "/api/search"
_TORRENTS_CSV_HOSTS = ("torrents-csv.com", "www.torrents-csv.com")
_TORRENTS_CSV_SEARCH_PATH = "/service/search"

# 归一化后的站点名：以主机名为准（配置页输入框里显示的是完整地址）。
_HOST_NAMES = {
    "m.diao.im": "diao.im",
    "torrents-csv.com": "torrents-csv",
}


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


def _host_and_path(url):
    """取主机名与路径；URL 已经过 `_normalise_url`，这里只做形状上的读取。

    按字符串剥掉 scheme 再切，不用 `urlsplit`：只需要主机名与路径两段，字符串切分
    的语义更直白，也不必处理 `SplitResult` 的各字段默认值。
    """
    restored = _SCHEME_RE.sub("", url or "", count=1)
    host, _, rest = restored.partition("/")
    path = "/" + rest.split("?", 1)[0] if rest else ""
    return host.lower(), path


def classify_site_url(url):
    """一个完整站点地址 → (kind, base_url, web_search_url)。

    ``kind`` 为空串表示这个地址不是磁力入口，只当云盘资源页模板用。判定规则：

    1. 带 ``{q}`` 的地址 = 云盘搜索页（同一个地址既不能当磁力接口，也不能两用）。
    2. 主机是 diao（``m.diao.im`` / ``diao.im``）= diao 磁力站；只填主机时补成默认的
       ``/api/search``，与内置默认站同一个入口。
    3. 主机是 ``torrents-csv.com`` = torrents-csv 磁力站；只填主机时补成默认的
       ``/service/search``。
    4. 其余地址判定不出入口，返回三个空串，由调用方决定报错还是沿用显式声明的 kind。
    """
    host, path = _host_and_path(url)
    if "{q}" in url:
        return "", "", url
    if host in _DIAO_HOSTS:
        if not path or path == "/":
            return DIAO_KIND, url.rstrip("/") + _DIAO_SEARCH_PATH, ""
        return DIAO_KIND, url, ""
    if host in _TORRENTS_CSV_HOSTS:
        if not path or path == "/":
            return TORRENTS_CSV_KIND, url.rstrip("/") + _TORRENTS_CSV_SEARCH_PATH, ""
        return TORRENTS_CSV_KIND, url, ""
    return "", "", ""


def site_name_for(url, fallback=""):
    """站点显示名：以主机名为准（``m.diao.im`` 收成 ``diao.im``）。"""
    host, _ = _host_and_path(url)
    if host in _HOST_NAMES:
        return _HOST_NAMES[host]
    if host:
        return host
    return fallback


def describe_site(site):
    """给配置页回显用：补上按地址识别出的 kind 与显示名，不改动已明确的字段。"""
    if not isinstance(site, dict):
        return site
    kind = site.get("kind") or ""
    base_url = site.get("base_url") or ""
    web_search_url = site.get("web_search_url") or ""
    if not kind and base_url:
        inferred_kind, inferred_base, inferred_web = classify_site_url(base_url)
        kind = inferred_kind
        base_url = inferred_base or base_url
        web_search_url = web_search_url or inferred_web
    host_name = site_name_for(base_url or web_search_url)
    return {
        "name": host_name or site.get("name") or kind or "网页搜索站",
        "kind": kind,
        "base_url": base_url,
        "web_search_url": web_search_url,
    }


def normalise_site(raw):
    """一条站点配置 → (site, error)。字段缺失按规则补，非法就整条拒绝。

    一个站至少要有一个入口（接口地址或网页搜索地址）。**只有网页搜索地址的站是合法的**
    ——那是「只发云盘资源、没有搜索 API」的站，也是云盘通道真正常见的入口；它的 `kind`
    没有可解析的响应，归一化成空串，取源侧不会把它当成磁力站。

    配置页只发一个地址（``base_url``）。``kind`` 缺省时按 ``classify_site_url`` 判定；
    显式给了 ``kind`` 但地址判定不出入口时**保留调用方的声明**（旧 XML 与脚本仍可用）；
    两者都没有就是一条配不出来的配置，直接报错——静默当成 diao 会让一个打不通的地址
    看起来像「这个站没有资源」。
    """
    if not isinstance(raw, dict):
        return None, f"每个资源站必须是对象，收到 {type(raw).__name__}"
    base_url, error = _normalise_url(raw.get("base_url"))
    if error:
        return None, error
    web_search_url, error = _normalise_url(raw.get("web_search_url"))
    if error:
        return None, error
    if web_search_url and "{q}" not in web_search_url:
        return None, f"网页搜索地址必须包含 {{q}} 占位符：{web_search_url!r}"
    if not base_url and not web_search_url:
        return None, "站点至少要填「接口地址」或「网页搜索地址」中的一个"

    declared_kind = _clean_text(raw.get("kind"), MAX_NAME_LEN).lower()
    inferred_kind, inferred_base, inferred_web = classify_site_url(base_url) if base_url else ("", "", "")
    if base_url:
        # 有地址就按地址判定入口：带 {q} 的那些变成云盘页（没有磁力入口），认得出的主机
        # 补上默认接口路径。
        if inferred_web:
            base_url = ""
            web_search_url = web_search_url or inferred_web
            kind = ""
        else:
            base_url = inferred_base or base_url
            if inferred_kind:
                kind = inferred_kind
            elif declared_kind:
                # 显式声明优先（老 XML / 脚本）：地址认不出来时按声明走，别把已有配置变成
                # 启动就报错。配置页保存前会先拒掉认不出来的地址，走不到这个分支。
                kind = declared_kind
            else:
                return None, (
                    f"识别不出这个资源站该走哪个入口：{base_url!r}"
                    f"（支持 {'/'.join(SITE_KINDS)} 的接口地址，或带 {{q}} 占位符的网页搜索地址）"
                )
    else:
        # 没有接口地址：旧形态的云盘专用站。没有响应可解析，kind 归零。
        kind = ""

    if base_url and kind not in SITE_KINDS:
        return None, f"未知的资源站类型 {kind!r}（支持 {'/'.join(SITE_KINDS)}）"
    if not base_url:
        kind = ""

    name = (_clean_text(raw.get("name"), MAX_NAME_LEN)
            or site_name_for(base_url or web_search_url)
            or kind
            or "网页搜索站")
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


def magnet_sites(sites):
    """(api_sites, extra_sites)：有接口地址、且 kind 已知的那些站。

    只填网页搜索地址的云盘专用站两个都不是——它不参与磁力取源，也不该占一份预算切片。
    """
    api_sites = [
        site for site in sites or []
        if site.get("base_url") and site.get("kind") == DIAO_KIND
    ]
    extra_sites = [
        site for site in sites or []
        if site.get("base_url") and site.get("kind") == TORRENTS_CSV_KIND
    ]
    return api_sites, extra_sites


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
