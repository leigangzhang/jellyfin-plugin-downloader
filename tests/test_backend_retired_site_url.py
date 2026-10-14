"""站点地址到入口的判定：`classify_site_url` / `normalise_site` / `describe_site`。

Subject: `backend/source_config.py` 的地址分类语义（配置页一个站只有一个地址输入框，
所以「地址 → 哪个入口」必须由地址本身判定）。

页面与 C# 侧的源码守卫在 `test_backend_retired_backend_contract.py` 与
`test_backend_retired_config_page.py`；共用夹具在 `tests/_backend_retired_helpers.py`。
"""

from __future__ import annotations

import unittest

from tests._backend_retired_helpers import source_config


class SiteUrlClassificationTests(unittest.TestCase):
    """配置页一个站只有一个地址输入框，所以「地址 → 哪个入口」必须由地址本身判定。

    `classify_site_url` 是这条判定的唯一事实源：配置页显示识别结果、C# 保存时补 kind、
    `normalise_site` 归一化都按它走。判定错一格，用户粘进去的站就会走错适配器。
    """

    def test_a_bare_host_gets_the_builtin_search_path(self):
        """只填主机名时，要落到与内置默认站同一个入口。"""
        self.assertEqual(source_config.classify_site_url("https://m.diao.im"),
                         ("diao", "https://m.diao.im/api/search", ""))
        self.assertEqual(source_config.classify_site_url("https://torrents-csv.com"),
                         ("torrents-csv", "https://torrents-csv.com/service/search", ""))

    def test_an_explicit_path_is_kept_verbatim(self):
        self.assertEqual(source_config.classify_site_url("https://m.diao.im/api/search"),
                         ("diao", "https://m.diao.im/api/search", ""))
        self.assertEqual(source_config.classify_site_url("https://torrents-csv.com/service/search"),
                         ("torrents-csv", "https://torrents-csv.com/service/search", ""))

    def test_a_query_template_is_a_cloud_page(self):
        kind, base_url, web = source_config.classify_site_url("https://s.example/search?q={q}")
        self.assertEqual((kind, base_url), ("", ""))
        self.assertEqual(web, "https://s.example/search?q={q}")

    def test_an_unknown_host_is_not_claimed_as_a_magnet_station(self):
        self.assertEqual(source_config.classify_site_url("https://unknown.example/x"), ("", "", ""))

    def test_normalise_site_takes_the_single_url_the_page_sends(self):
        """配置页只发 BaseUrl（kind 空、web_search_url 空），这里要能补全。"""
        site, error = source_config.normalise_site({"name": "", "kind": "", "base_url": "https://m.diao.im"})
        self.assertEqual(error, "")
        self.assertEqual(site["kind"], "diao")
        self.assertEqual(site["base_url"], "https://m.diao.im/api/search")
        self.assertEqual(site["name"], "diao.im")

        cloud, error = source_config.normalise_site({"name": "", "kind": "", "base_url": "https://pan.example/s?q={q}"})
        self.assertEqual(error, "")
        self.assertEqual(cloud["kind"], "")
        self.assertEqual(cloud["base_url"], "")
        self.assertEqual(cloud["web_search_url"], "https://pan.example/s?q={q}")

    def test_legacy_dual_entry_sites_keep_the_magnet_entry(self):
        """老 XML 里同时有接口地址与网页地址的站，不能因为这次归一化丢掉磁力入口。"""
        site, error = source_config.normalise_site({
            "name": "A", "kind": "diao", "base_url": "https://a.example/api",
            "web_search_url": "https://a.example/s?q={q}",
        })
        self.assertEqual(error, "")
        self.assertEqual(site["base_url"], "https://a.example/api")
        self.assertEqual(site["kind"], "diao")
        self.assertEqual(site["web_search_url"], "https://a.example/s?q={q}")

    def test_an_unknown_kind_is_still_rejected(self):
        """「配了但没人认账的类型」不能静默通过。"""
        site, error = source_config.normalise_site({"kind": "nope", "base_url": "https://x/y"})
        self.assertIsNone(site)
        self.assertIn("未知的资源站类型", error)

    def test_an_unknown_host_without_a_declared_kind_is_rejected(self):
        """认不出来的地址 + 没声明类型 = 配置错误。

        静默兜成 diao 会让 `https://google.com` 变成「一个打不通的 diao 站」，
        用户只看到「搜了但没结果」。这条守卫就钉住「不许静默兜底」。
        """
        site, error = source_config.normalise_site({"kind": "", "base_url": "https://google.com"})
        self.assertIsNone(site)
        self.assertIn("识别不出", error)
        # 没有 kind 键也一样（配置页发的就是这个形状）
        site, error = source_config.normalise_site({"base_url": "https://unknown.example/x"})
        self.assertIsNone(site)
        self.assertIn("识别不出", error)

    def test_an_explicit_kind_still_carries_a_self_hosted_station(self):
        """老 XML / 脚本显式声明了类型时，认不出的主机按声明走，不被这次收紧打掉。"""
        site, error = source_config.normalise_site(
            {"name": "自建", "kind": "diao", "base_url": "https://self.example/api"})
        self.assertEqual(error, "")
        self.assertEqual(site["kind"], "diao")
        self.assertEqual(site["base_url"], "https://self.example/api")

    def test_describe_site_states_the_kind_the_page_shows(self):
        described = source_config.describe_site({
            "name": "", "kind": "", "base_url": "https://torrents-csv.com",
            "web_search_url": "",
        })
        self.assertEqual(described["kind"], "torrents-csv")
        self.assertEqual(described["name"], "torrents-csv")
        self.assertEqual(described["base_url"], "https://torrents-csv.com/service/search")
        page = source_config.describe_site({
            "name": "搜索页", "kind": "", "base_url": "",
            "web_search_url": "https://s.example/s?q={q}",
        })
        self.assertEqual(page["kind"], "")
        self.assertEqual(page["name"], "s.example")
        self.assertEqual(page["web_search_url"], "https://s.example/s?q={q}")


if __name__ == "__main__":
    unittest.main()
