"""`/api/search` 的单源默认值：按类型问 `source_config`，缺省仍是 True。

Subject: `backend/console_server/http_api.py` 的 `_api_search` 与它调用的
`resolve_publisher_only`。

`publisher_only=true` 会把搜索阶梯裁成只剩「片名 + 发布者署名」那一档
（`search_pool.py` 的 anchor tier）。三个 L1 关键字（高清剧集网 / DDHDTV / BBHDTV）
是剧集发布组的签名，实测 35 部电影片名里 anchor 档真片命中 0/35 —— 所以「电影」
这一类的缺省值不该继续跟着剧集走，改由 `source_config.publisher_only_default(kind)`
决定。三条边界必须同时成立：

* 显式传了 `publisher_only` 的请求**以请求为准**（既有调用方不受影响）；
* `source_config` 的接口没就绪时退回 True（= 修复前的行为，不是崩）；
* kind 先归一化再查表（`" Movie "` 与 `"movie"` 必须拿到同一个答案）。
"""

from __future__ import annotations

import unittest
from unittest import mock

from tests.backend_download._backend_download_helpers import (
    HandlerStub,
    http_api,
    source_config,
)

BASE = {"title": "一战再战", "kind": "movie", "year": "2025", "sources": "magnet"}


def search(body):
    """跑一次 `_api_search`，返回 (stub, 捕获到的 params 列表)。"""
    stub = HandlerStub()
    captured = []
    with mock.patch.object(http_api, "valid_target_dir", lambda path: True), \
         mock.patch.object(http_api, "dispatch_search",
                           lambda jid, params: captured.append(params)):
        http_api.ConsoleHandler._api_search(stub, body)
    return stub, captured


class PublisherOnlyDefaultTests(unittest.TestCase):
    def test_explicit_request_wins_over_the_kind_default(self):
        with mock.patch.object(source_config, "publisher_only_default",
                               lambda kind: False, create=True):
            _, captured = search({**BASE, "publisher_only": True})
        self.assertIs(captured[0]["publisher_only"], True,
                      "请求显式传了就以请求为准，不能被按类型的缺省值顶掉")

    def test_explicit_false_request_wins_too(self):
        with mock.patch.object(source_config, "publisher_only_default",
                               lambda kind: True, create=True):
            _, captured = search({**BASE, "publisher_only": False})
        self.assertIs(captured[0]["publisher_only"], False)

    def test_kind_default_comes_from_source_config(self):
        resolve = mock.MagicMock(return_value=False)
        with mock.patch.object(source_config, "publisher_only_default",
                               resolve, create=True):
            _, captured = search(BASE)
        self.assertIs(captured[0]["publisher_only"], False)
        self.assertEqual(resolve.call_args.args[0], "movie")

    def test_kind_is_normalized_before_the_lookup(self):
        resolve = mock.MagicMock(return_value=False)
        with mock.patch.object(source_config, "publisher_only_default",
                               resolve, create=True):
            _, captured = search({**BASE, "kind": " Movie "})
        self.assertEqual(resolve.call_args.args[0], "movie",
                         "归一化后查表：' Movie ' 与 'movie' 必须同一个答案")
        self.assertIs(captured[0]["publisher_only"], False)

    def test_missing_interface_falls_back_to_single_source(self):
        """W2 的接口还没落地时：行为必须与修复前一致（True），而不是 AttributeError。"""
        had = hasattr(source_config, "publisher_only_default")
        saved = getattr(source_config, "publisher_only_default", None)
        if had:
            delattr(source_config, "publisher_only_default")
        try:
            _, captured = search(BASE)
        finally:
            if had:
                setattr(source_config, "publisher_only_default", saved)
        self.assertIs(captured[0]["publisher_only"], True)

    def test_broken_interface_falls_back_and_warns(self):
        def boom(kind):
            raise TypeError("unexpected keyword argument")

        with mock.patch.object(source_config, "publisher_only_default",
                               boom, create=True):
            _, captured = search({**BASE, "kind": "episode"})
        self.assertIs(captured[0]["publisher_only"], True,
                      "接口报错不能拖垮搜索，退回单源默认（旧行为）")


class ResolvePublisherOnlyUnitTests(unittest.TestCase):
    """直接钉住接线函数本身，免得只靠 HTTP 面间接断言。"""

    def test_explicit_key_in_body_means_yes_or_no_verbatim(self):
        self.assertIs(http_api.resolve_publisher_only("movie", {"publisher_only": 0}), False)
        self.assertIs(http_api.resolve_publisher_only("movie", {"publisher_only": 1}), True)

    def test_absent_key_asks_the_config(self):
        with mock.patch.object(source_config, "publisher_only_default",
                               lambda kind: kind == "movie", create=True):
            self.assertIs(http_api.resolve_publisher_only("movie", {}), True)
            self.assertIs(http_api.resolve_publisher_only("episode", {}), False)


if __name__ == "__main__":
    unittest.main()
