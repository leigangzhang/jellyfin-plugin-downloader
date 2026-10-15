"""`tests/backend_trending/` 的共享件：backend/ 路径、假 TMDb opener、临时数据根。

这里没有用例（`unittest discover` 只收 `test*.py`）。这条写线的 inScope 只给了
`__init__.py` + `test_backend_trending_*.py`，所以共享件放在包初始化里，而不是
兄弟目录那种 `_backend_*_helpers.py`。

三样东西：

* `backend/` 进 `sys.path` —— 被测对象是插件后端，不在 `tests/harness.py` 那个根里；
* `FakeTMDb` / `FakeResponse` —— TMDb 的替身：记下每次请求、按端点回放造好的 JSON，
  也可以切换成「HTTP 错误 / 网络错误 / 坏 JSON / 非 200」。被测的是整条链路
  （路由 → 缓存 → 取源），替身只换掉进程外那一段 HTTP，下游全部是真的；
* `temp_data_root` —— 把 `trending_cache` / `trending_watchlist` 的 `DATA_ROOT`
  指到临时目录（`mkdtemp` 每个用例一份），测试因此不碰真实 `backend/state`。
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import console_server.http_api as http_api  # noqa: E402,F401  （再导出，供用例直接用）
import media_download_lib  # noqa: E402,F401  （确认 backend/ 在路径上）
import trending.trending_cache as trending_cache  # noqa: E402
import trending.trending_source as trending_source  # noqa: E402
import trending.trending_watchlist as trending_watchlist  # noqa: E402

TMDb_ENDPOINT_BASE = {
    "/3/trending/movie/week": 11000,
    "/3/trending/tv/week": 12000,
    "/3/movie/popular": 13000,
    "/3/tv/popular": 14000,
}
TV_ENDPOINTS = ("/3/trending/tv/week", "/3/tv/popular")
ITEM_KEYS = {
    "tmdb_id",
    "title",
    "original_title",
    "year",
    "kind",
    "poster_path",
    "vote_average",
}


class FakeResponse:
    """`urlopen` 返回值的最小替身：`read()` + `status` + `close()`。"""

    def __init__(self, body, status=200):
        self._body = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.status = status
        self.closed = False

    def read(self):
        return self._body

    def close(self):
        self.closed = True


def tmdb_payload(path, count=20):
    """按端点造一份 TMDb 形状的 JSON：电影用 title/release_date，剧集用 name/first_air_date。"""
    base = TMDb_ENDPOINT_BASE.get(path)
    if base is None:
        return {"page": 1, "total_pages": 1, "total_results": 0, "results": []}
    is_tv = path in TV_ENDPOINTS
    results = []
    for index in range(count):
        tmdb_id = base + index
        month = index % 9 + 1
        if is_tv:
            results.append(
                {
                    "id": tmdb_id,
                    "name": f"剧集 {tmdb_id}",
                    "original_name": f"Original Series {tmdb_id}",
                    "first_air_date": f"{1990 + index % 20}-{month:02d}-02",
                    "poster_path": f"/poster{tmdb_id}.jpg",
                    "vote_average": round(6.0 + (index % 10) / 10, 1),
                }
            )
        else:
            results.append(
                {
                    "id": tmdb_id,
                    "title": f"电影 {tmdb_id}",
                    "original_title": f"Original Movie {tmdb_id}",
                    "release_date": f"{2000 + index % 20}-{month:02d}-15",
                    "poster_path": f"/poster{tmdb_id}.jpg",
                    "vote_average": round(7.0 + (index % 10) / 10, 1),
                }
            )
    return {
        "page": 1,
        "total_pages": 1,
        "total_results": len(results),
        "results": results,
    }


class FakeTMDb:
    """假 TMDb：`opener` 可直接当 `opener=` 传进被测代码。

    `error` 非空时每次调用都抛它（模拟 TMDb 不可达）；`status` 非 200 时回一份
    带该状态码的响应（模拟「不抛异常的失败」）；`body` 覆盖响应体（模拟坏 JSON）。
    """

    def __init__(self, per_list=20, status=200, error=None, body=None):
        self.calls = []
        self.per_list = per_list
        self.status = status
        self.error = error
        self.body = body

    def opener(self, request, timeout=None):
        url = request.full_url
        self.calls.append(
            {"url": url, "timeout": timeout, "ua": request.get_header("User-agent")}
        )
        if self.error is not None:
            raise self.error
        if self.body is not None:
            return FakeResponse(self.body, status=self.status)
        path = urlparse(url).path
        return FakeResponse(
            json.dumps(tmdb_payload(path, self.per_list)), status=self.status
        )

    @property
    def urls(self):
        return [call["url"] for call in self.calls]

    @property
    def paths(self):
        return [urlparse(url).path for url in self.urls]


@contextlib.contextmanager
def temp_data_root():
    """每个用例一份临时数据根；只在用例内替换模块级 `DATA_ROOT`。"""
    with tempfile.TemporaryDirectory(prefix="jmd-trending-test-") as root:
        with mock.patch.object(trending_cache, "DATA_ROOT", root), mock.patch.object(
            trending_watchlist, "DATA_ROOT", root
        ):
            yield root


class HandlerStub(http_api.ConsoleHandler):
    """替身 Handler：真的继承 `ConsoleHandler`，只换掉出口与 `__init__`。

    继承是必要的：`do_GET` / `do_POST` 会在 `self` 上找 `_get_trending` 这类处理器
    （换成普通对象就会 `AttributeError`），而真实实例要一路造 socket。这里覆盖
    `__init__`（不调 `BaseHTTPRequestHandler.__init__`）与 `_send` / `_ok` / `_bad` /
    `_body`：分派表、`parse_qs`、`urlparse`、两条路由的处理器全都是真的。
    """

    def __init__(self, path="/", body=None):
        self.path = path
        self.stub_body = {} if body is None else body
        self.sent = []

    def _send(self, status, payload, content_type="application/json; charset=utf-8"):
        self.sent.append((status, payload))

    def _ok(self, obj):
        self.sent.append((200, obj))

    def _bad(self, msg):
        self.sent.append((400, {"error": msg}))

    def _body(self):
        return self.stub_body

    @property
    def last(self):
        return self.sent[-1] if self.sent else (None, None)

    @property
    def status(self):
        return self.last[0]

    @property
    def payload(self):
        return self.last[1]


def trending_tmp_files(root):
    """数据根下遗留的临时文件（原子写应该在 `os.replace` 后不留任何 `.tmp`）。"""
    found = []
    for directory, _, names in os.walk(root):
        found += [
            os.path.join(directory, name) for name in names if name.endswith(".tmp")
        ]
    return found
