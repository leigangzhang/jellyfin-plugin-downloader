"""配置页 / 插件侧（C#）与后端之间的跨层契约。

Subject: `Configuration/configPage.html`、`Controllers/JellyfinDownloaderController.cs`、
`BackendManager.cs` 与 `backend/` 之间那些「两边必须逐字对齐」的字面量契约——
其中一个真实后端会按代理的拼法被打一发真实 HTTP 请求。
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

from tests._backend_retired_helpers import (
    BACKEND,
    ROOT,
    anonymous_object_keys,
    pick_free_port,
    source_config,
)


class BackendContractGuardTests(unittest.TestCase):
    """配置页 / 插件侧（C#）与后端之间的跨层契约守卫。

    页面只有一个地址输入框、代理只认实际监听端口、后端子进程只拿白名单环境、
    JSON 响应里的键不许只差大小写——这四类失败都发生在两层之间，单侧测试看不见。
    """

    def test_the_page_cannot_save_an_address_the_backend_cannot_classify(self):
        """配置页侧的同一条守卫：页面恒发 Kind=""，所以未知主机必须被 C# 校验拒掉。

        用源码断言而不是起 Jellyfin：改造后 C# 的「认不出来」必须走 KindForUrl 返回
        空串的分支，不能自己兜一个 diao 出来。
        """
        controller = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "Controllers"
                      / "JellyfinDownloaderController.cs").read_text(encoding="utf-8")
        self.assertIn("private static string KindForUrl(string url)", controller)
        self.assertIn("KindForUrl(address).Length == 0", controller,
                      "ValidateBtSites 必须用 KindForUrl 的空串结果拦下未知地址")
        self.assertIn("识别不出这个地址该走哪个入口", controller)

    def test_the_page_round_trips_the_station_kind(self):
        """手改 XML 的自建站（未知主机 + 显式 kind）不能被配置页一次保存搞丢。

        页面只给一个地址输入框，类型没有可编辑的入口；所以后端回显的 kind 必须被
        原样存回来，并由 save 原样发出去——否则 ValidateBtSites 会把整单拒掉，
        连同一个页面里的其它修改也一起存不下去。
        """
        page = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "Configuration" / "configPage.html").read_text(encoding="utf-8")
        self.assertIn("row._kind", page, "站点行必须记住后端给的 kind")
        self.assertIn("Kind: item.kind", page, "save 必须把 kind 原样发回")
        self.assertIn("refreshSiteHint(row)", page, "自定义站要按声明类型显示")

    def test_the_station_box_shows_the_station_not_the_api_path(self):
        """资源站点框里显示站点本身；完整接口路径由后端拼接并保存。

        需求：页面上写 `https://m.diao.im`，`/api/search` 在后台补。
        所以 (a) C# 默认给的是裸站点；(b) 页面把已知站点的默认路径折回站点；
        (c) 保存时后端（normalise_site / NormaliseSite）会补回完整路径。
        """
        page = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "Configuration" / "configPage.html").read_text(encoding="utf-8")
        self.assertIn("function siteShortUrl(address)", page)
        self.assertIn("SITE_PATHS", page, "页面要认得哪些是内置站点的默认接口路径")
        for host_path in ("'/api/search'", "'/service/search'"):
            self.assertIn(host_path, page)

        import source_config

        # 裸站点经后端归一化后，必须落回 source_config 的默认接口地址
        for site in source_config.DEFAULT_SITES:
            bare = "https://" + site["base_url"].split("/")[2]
            normalised, error = source_config.normalise_site({"kind": "", "base_url": bare})
            self.assertEqual(error, "", f"{bare} 应当能被归一化")
            self.assertEqual(normalised["base_url"], site["base_url"],
                             f"{bare} 归一化后必须等于内置默认的接口地址")

    def test_the_http_proxy_follows_the_effective_port(self):
        """面板的数据面调用必须走「实际监听端口」，不能走配置里的固定 URL。

        `PluginConfiguration.BackendBaseUrl` 全仓没有写入点（永远停在 8123），而端口
        被占用时后端会自增到 8124——代理照那个字段转发的话，面板的 snapshot/search/
        submit/watch/verify/mark/cleanup/pan 全部 502，「端口占用自增」端到端失效。
        这里同时钉住两侧：C# 代理不得再直接读那个字段，且代理拼出的目标地址在
        自增端口上真的能打到后端。
        """
        controller = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "Controllers"
                      / "JellyfinDownloaderController.cs").read_text(encoding="utf-8")
        proxy = controller[controller.index("public async Task Proxy(string path)"):]
        proxy = proxy[:proxy.index("private static bool IsAllowed")]
        # 只看可执行代码，注释里点名这个字段是允许的（那是写给下一个读者的）
        code = "\n".join(line for line in proxy.splitlines()
                         if not line.strip().startswith("//"))
        self.assertNotIn("BackendBaseUrl", code, "代理必须走 BackendManager.BaseUrl")
        self.assertIn("var baseUrl = BackendManager.BaseUrl;", code)

        # 行为侧：起一个真实后端，把「自增端口」当 EffectivePort，按代理的拼法打一发
        import subprocess
        import tempfile
        import urllib.request

        with tempfile.TemporaryDirectory() as tmp:
            port = pick_free_port()
            env = dict(os.environ, JMD_DATA_DIR=tmp)
            server = subprocess.Popen(
                [sys.executable, "-u", str(BACKEND / "console_server.py"),
                 "--port", str(port)],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                # 事件驱动等启动：后端就绪时会打出含端口的启动行，读出它即可，
                # 不轮询、不 sleep（`tools/check-invariants.py` 有 no-time-based-test-sync）。
                ready = ""
                for line in server.stdout:
                    if f":{port}/" in line or "JMD 控制台" in line:
                        ready = line.strip()
                        break
                self.assertTrue(ready, "后端没有打出启动行")

                # 代理拼法（Controller.Proxy）：baseUrl + "/api/" + path + query
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/snapshot?slug=jdlping")
                with urllib.request.urlopen(request, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual(payload.get("service"), "jellyfin-downloader-backend")
                self.assertIn("candidates", payload, "打到的不像快照响应")
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()

    def test_paths_with_spaces_reach_the_backend_unharmed(self):
        """媒体根含空格（如 /Volumes/XIAOMI SSD2/Media）时必须完整传到后端。

        `BackendManager.Start` 把 python / 脚本 / 日志拼进 `/bin/sh -c` 的字符串，
        所以任何拼进去的值都会被 shell 再解析一遍：不加引号的路径会被空格切成两半
        （实机踩到过——两个环境变量都变成 /Volumes/XIAOMI，下载会写到错的地方）。
        这里用带空格的路径过一遍真实的拼串与 shell 解析。
        """
        import shlex
        import subprocess

        media_root = "/Volumes/XIAOMI SSD2/Media"
        staging_root = "/Volumes/XIAOMI SSD2/Media/.staging"
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "console_server.py"
            script.write_text("import os\nprint(repr(os.environ.get('JMD_MEDIA_ROOT')))\n",
                              encoding="utf-8")
            log = Path(tmp) / "backend.log"

            # 与 BackendManager.Start 同一拼法：三条路径必须各自加单引号。
            # 先钉住生产代码里真的是这个形状，下面跑的才算「生产拼法」而不是测试自己复读
            # 一遍引号（否则把 C# 里的引号删掉，这条用例照样绿，就失去守卫意义了）。
            manager = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "BackendManager.cs").read_text(encoding="utf-8")
            self.assertIn(
                """var command = $"exec '{python}' '{script}' >> '{log}' 2>&1";""",
                manager, "python / 脚本 / 日志三条路径必须各自加单引号")
            command = "exec '%s' '%s' >> '%s' 2>&1" % (sys.executable, script, log)
            env = dict(os.environ,
                       JMD_MEDIA_ROOT=media_root,
                       JMD_STAGING_ROOT=staging_root,
                       JMD_PORT="8124")
            proc = subprocess.run(["/bin/sh", "-c", command], env=env,
                                  capture_output=True, text=True, timeout=30)

            self.assertEqual(proc.returncode, 0, proc.stderr)
            # 启动行把 stdout 重定向进日志（与 BackendManager 的拼法一致），所以读日志
            self.assertTrue(log.exists(), "日志重定向没生效")
            self.assertEqual(log.read_text(encoding="utf-8").strip(), repr(media_root),
                             "媒体根被 shell 切断了——下载会落到错误目录")
            # 整条命令行按 shell 规则切出来的 argv：三条路径各占一个参数，不多不少
            argv = shlex.split(command)
            self.assertEqual(argv[0], "exec")
            self.assertIn(script.as_posix(), argv)
            self.assertIn(log.as_posix(), argv)
            self.assertEqual(env["JMD_STAGING_ROOT"], staging_root)

        # 端口不再拼进命令行，而是走后端读得到的 JMD_PORT
        manager = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "BackendManager.cs").read_text(encoding="utf-8")
        self.assertIn('info.Environment["JMD_PORT"]', manager)
        self.assertNotIn("--port {port}", manager)
        backend = (BACKEND / "console_server.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("JMD_PORT")', backend)

    def test_the_backend_process_does_not_inherit_the_jellyfin_environment(self):
        """后端子进程只拿到白名单环境，不继承 Jellyfin 的凭据环境。

        实机复核发现：`ProcessStartInfo` 默认把父进程整份环境交给子进程，而 Jellyfin
        的环境里有它自己的 API key / 会话 token —— `ps eww` 里能在后端的 Python
        进程上看到那些名字。后端只需要 JMD_* 配置，没有理由继承这些。
        """
        manager = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "BackendManager.cs").read_text(encoding="utf-8")
        start = manager[manager.index("public static (bool Ok, string Detail) Start()"):]
        start = start[:start.index("private static string SerializeBtSites")]
        # 清空继承环境 + 只补白名单
        self.assertIn("info.Environment.Clear();", start,
                      "后端启动前必须清空继承来的环境变量")
        self.assertIn("foreach (var name in InheritedEnvWhitelist)", start)
        whitelist_block = manager[manager.index("private static readonly string[] InheritedEnvWhitelist"):]
        whitelist = re.findall(r'"([A-Z_]+)"', whitelist_block[:whitelist_block.index("};")])
        # 白名单只许放运行时必需项，任何凭据类名字都不许进
        forbidden = [name for name in whitelist
                     if any(token in name for token in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"))]
        self.assertEqual(forbidden, [], f"白名单里混进了凭据类变量：{forbidden}")
        self.assertTrue({"PATH", "HOME"}.issubset(set(whitelist)), whitelist)

    def test_the_page_shows_the_builtin_defaults_when_config_is_empty(self):
        """配置为空时页面要显示内置默认站点/关键词，而不是一排空输入框。

        需求是「系统内置两个资源站点，默认显示完整站点名」。后端在配置为空时会用
        内置默认，但那只发生在启动参数里——页面照 `btSites` 渲染，空了就是空白，
        用户看不到默认值。所以 C# 侧也存了一份默认，这里钉住它与 Python 侧逐字一致
        （两份默认一旦分叉，页面显示的和后端实际用的就不是一回事）。
        """
        import source_config

        controller = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "Controllers"
                      / "JellyfinDownloaderController.cs").read_text(encoding="utf-8")
        sites_block = controller[controller.index("private static readonly string[] DefaultSiteUrls"):]
        keywords_block = sites_block[sites_block.index("private static readonly string[] DefaultKeywords"):]
        cs_urls = re.findall(r'"(https://[^"]+)"', sites_block[:sites_block.index("DefaultKeywords")])
        cs_keywords = re.findall(r'"([^"]+)"', keywords_block[:keywords_block.index("};")])

        self.assertEqual(
            [source_config.classify_site_url(url)[1] for url in cs_urls],
            [site["base_url"] for site in source_config.DEFAULT_SITES],
            "C# 内置默认站点归一化后必须与 source_config.DEFAULT_SITES 指向同一接口")
        self.assertEqual(cs_keywords, list(source_config.DEFAULT_ANCHOR_KEYWORDS),
                         "C# 内置默认关键词与 source_config.DEFAULT_ANCHOR_KEYWORDS 不一致")
        # 空配置时走默认这一分支必须存在
        self.assertIn("DefaultSiteUrls", controller)
        self.assertIn("config?.BtSites is { Count: > 0 }", controller)
        self.assertIn("config?.L1Keywords is { Count: > 0 }", controller)

    def test_no_json_payload_has_case_colliding_keys(self):
        """JSON 响应里不许出现「只差大小写」的两个键。

        实机踩到过：`GetConfig` 的匿名对象里同时留着 `btSites` 与 `BtSites`，
        System.Text.Json 的冲突检测直接抛异常 → `/JellyfinDownloader/config` 整个 500
        → 配置页读不到任何配置（站点/关键词列表整块不渲染、端口显示 —）。
        编译器不报、构建与单测都不红，所以单独立一条守卫盯着。
        """
        offenders = []
        for path in sorted((ROOT / "Jellyfin.Plugin.JellyfinDownloader").rglob("*.cs")):
            seen = {}
            for key in anonymous_object_keys(path.read_text(encoding="utf-8")):
                seen.setdefault(key.lower(), set()).add(key)
            for lower, variants in seen.items():
                if len(variants) > 1:
                    offenders.append(f"{path.name}: {sorted(variants)}")
        self.assertEqual(offenders, [],
                         "匿名对象里有只差大小写的键，序列化会 500：" + "; ".join(offenders))

    def test_the_config_endpoint_serves_one_site_field(self):
        """配置页读 `config.btSites`，所以这个 key 必须存在且唯一。"""
        controller = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "Controllers"
                      / "JellyfinDownloaderController.cs").read_text(encoding="utf-8")
        config_action = controller[controller.index("public IActionResult GetConfig()"):]
        config_action = config_action[:config_action.index("public IActionResult UpdateConfig")]
        self.assertIn("btSites = sites", config_action)
        self.assertNotIn("BtSites = config", config_action)
        page = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "Configuration" / "configPage.html").read_text(encoding="utf-8")
        self.assertIn("config.btSites", page, "配置页必须读这个唯一的字段名")

    def test_the_backend_announces_itself_with_the_same_marker_the_plugin_looks_for(self):
        """自身标识是 C# 与 Python 之间的字面量契约。

        插件侧靠它区分「我们的后端」和「恰好占着这个端口的外来 HTTP 服务」：
        两边字面量一旦不一致，插件就会认不出自己的后端（或反过来认错），
        而这两种失败在单侧测试里都看不见。
        """
        marker = "jellyfin-downloader-backend"
        backend = (BACKEND / "console_server.py").read_text(encoding="utf-8")
        manager = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "BackendManager.cs").read_text(encoding="utf-8")
        self.assertIn('SERVICE_ID = "%s"' % marker, backend)
        self.assertIn('ServiceMarker = "%s"' % marker, manager)
        # 后端要真的把它写进 JSON 响应；插件要真的解析响应体里的字段
        # （不能只读前若干字节：那个字段在 JSON 末尾）
        self.assertIn('{**obj, "service": SERVICE_ID}', backend)
        self.assertIn('TryGetProperty("service", out var service)', manager)


if __name__ == "__main__":
    unittest.main()
