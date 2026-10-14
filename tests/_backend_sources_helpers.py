"""共享假件与夹具：`tests/test_backend_sources*.py` 各模块共用一份，不再复制。

Subject: `backend/search_pool.py`（diao.im 翻页 + 关键字阶梯）与
`backend/torrents_csv_source.py`（第二条磁力通道）。这里没有任何用例：`_` 前缀不会被
`unittest discover` 收集，只放 HTTP 边界桩（`FakeHttp`/`FakeResponse`/`FakeClock`）、
夹具读取、站点清单编码与两个 `main()` 驱动器。

`tests/harness.py` 有意不用 -- 它加载的是套件自己的工具，而本组测试的被测对象是插件
后端，在那个根之外。后端路径在 import 时插进 `sys.path` 一次，`search_pool` 现有的
扁平 `media_download_lib` import 照运行时那样工作。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import threading
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jmd_sources"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import probe_magnets  # noqa: E402,F401 -- re-exported: 这一行保证 BACKEND 已进 sys.path
import search_pool  # noqa: E402
import source_config  # noqa: E402
import torrents_csv_source  # noqa: E402


NOW = 1_800_000_000.0  # fixed reference so snapshot ages are deterministic


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixture_hashes(payload):
    return [str(item.get("hash") or "").upper() for item in payload["data"]["torrents"]]


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHttp:
    """urlopen stand-in: answers with the canned payload matching the requested offset.

    It also models request duration, capped by the timeout the caller asked for,
    so a wall-clock budget can be proven without a real sleep.
    """

    def __init__(self, by_offset, delay=0.0, clock=None, missing_status=None, default=None):
        self.by_offset = by_offset
        self.delay = delay
        self.clock = clock
        self.missing_status = missing_status
        # `default` answers every offset the map does not cover (used when one
        # canned page should stand in for a whole query).
        self.default = default
        self.requests = []
        self._lock = threading.Lock()

    def __call__(self, request, timeout=None):
        with self._lock:
            self.requests.append({"url": request.full_url, "timeout": timeout,
                                  "user_agent": request.get_header("User-agent")})
        if self.clock is not None:
            self.clock.advance(min(self.delay, float(timeout or self.delay)))
        query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        offset = int(query.get("offset", ["0"])[0])
        payload = self.by_offset.get(offset, self.default)
        if payload is None:
            if self.missing_status is not None:
                raise search_pool.urllib.error.HTTPError(
                    request.full_url, self.missing_status, "missing", {}, None)
            raise OSError(f"no canned page for offset {offset}")
        return FakeResponse(json.dumps(payload).encode("utf-8"))


class FakeClock:
    """Manual monotonic clock for budget tests."""

    def __init__(self, start=0.0):
        self.value = start
        self._lock = threading.Lock()

    def __call__(self):
        return self.value

    def advance(self, seconds):
        with self._lock:
            self.value += seconds


def canned_pages(names):
    """offset -> payload, from the recorded fixtures (offsets 0/10/20)."""
    offsets = {"diao_page_offset0.json": 0,
               "diao_page_offset10.json": 10,
               "diao_page_offset20.json": 20}
    return {offset: load_fixture(name) for name, offset in offsets.items()}


def diao_payload(torrents, has_more=False):
    """A canned HTTP *body* for the diao index (`search_api` parses this shape)."""
    return {"status": 200,
            "data": {"keywords": [], "torrents": list(torrents), "has_more": has_more}}


def l1_torrent(info_hash, name, files=()):
    """One canned diao hit; `files` is what decides whether metadata is verified."""
    return {"hash": info_hash, "name": name, "size": 123456, "files": list(files),
            "files_count": len(files), "magnet_uri": f"magnet:?xt=urn:btih:{info_hash}"}


def external_result(entries):
    """A canned torrents-csv channel response."""
    return {"status": 200, "message": "", "query": "", "entries": list(entries),
            "elapsed_secs": 0.0}


def external_entry(info_hash=None, name=None, seeders=12, age_days=22.7, size=123456):
    """One torrents-csv entry in the shape `torrents_csv_source.search` returns."""
    resolved = info_hash or "E" * 40
    return {"info_hash": resolved, "name": name or f"External.Release.{resolved[:6]}.2160p",
            "size": size, "size_gb": round(size / (1024 ** 3), 3), "file_count": None,
            "seeders": seeders, "leechers": 1, "created_at": None,
            "seeders_snapshot_unix": int(NOW), "seeders_snapshot_at": "2026-09-20T00:00:00+00:00",
            "seeders_snapshot_age_days": age_days, "sources": ["torrents-csv"],
            "magnet": magnet_for(resolved)}


def magnet_for(info_hash):
    return f"magnet:?xt=urn:btih:{info_hash}"


DIAO_SITE = {"name": "diao.im", "kind": "diao",
             "base_url": "https://m.diao.im/api/search", "web_search_url": ""}


def sites_env(sites):
    """把站点清单编码成环境变量的形状（`source_config` 是唯一的读者）。"""
    return json.dumps(sites, ensure_ascii=False)


def run_search_main(argv, api, extra_search, env=None):
    """跑 `main()`：HTTP 边界与第二通道都换成桩，站点清单按 `env` 覆盖。

    两个环境变量**总是**被显式设成某个值（默认空串 = 用内置默认），这样宿主机上
    碰巧设了它们也不会改变测试行为。
    """
    full_env = {source_config.ENV_SITES: "", source_config.ENV_KEYWORDS: ""}
    full_env.update(env or {})
    stdout = io.StringIO()
    with mock.patch.object(sys, "argv", argv), \
            mock.patch.dict(os.environ, full_env, clear=False), \
            mock.patch.object(search_pool.urllib.request, "urlopen", api), \
            mock.patch.object(torrents_csv_source, "search", extra_search), \
            contextlib.redirect_stdout(stdout):
        code = search_pool.main()
    return code, stdout.getvalue()


def run_main_with_fakes(argv, page, extra_entries, env=None):
    """Run `main()` with the HTTP boundary and the second channel stubbed.

    The L1 side goes through the real `search_api` on purpose:
    `fetch_query_pages` takes `search_api` as a default argument, so patching the
    module attribute would not reach the pager and the test would quietly talk to
    the live index instead. One canned page (`page`) answers every offset, and
    `has_more` is false, so each query costs exactly one request.
    """
    return run_search_main(
        argv, FakeHttp({}, default=page),
        lambda *a, **k: external_result(extra_entries), env=env)
