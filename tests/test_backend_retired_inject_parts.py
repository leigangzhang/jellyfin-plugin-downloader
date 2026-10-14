"""装配守卫：/script 的分片、csproj 的声明、controller 的顺序表三方对账。

Web/inject.js 已按职责拆成 Web/inject_01_core.js … Web/inject_05_render.js，由
JellyfinDownloaderController.GetScript() 按 ScriptSegments 的顺序逐片 ReadResource 后整份
返回。分片各自不是完整脚本（第一片开 IIFE，最后一片收尾），所以「加了分片忘了登记」或
「顺序表里写了不存在的分片」都会让服务端静默少发代码：浏览器拿到缺块的脚本，而 C# 侧不报错。

运行时守卫（DescribeScriptSegmentMismatch）只有真的请求一次 /script 才会跑；这个模块把同一条
不变量钉在源码层，做纯文本对账（不编译、不启动 Jellyfin、不联网）：

  1. csproj 里 EmbeddResource 声明的分片，按 MSBuild 命名规则算出的资源名，必须与 controller 的
     `ScriptSegmentPrefix + ScriptSegments + ScriptSegmentSuffix` 逐一相同（顺序也相同）；
  2. Web/ 下的 inject_*.js 与被声明的分片是同一份清单；
  3. 已被分片取代的 Web/inject.js 不能回来。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "Jellyfin.Plugin.JellyfinDownloader"
WEB_DIR = PLUGIN_DIR / "Web"
CSPROJ = PLUGIN_DIR / "Jellyfin.Plugin.JellyfinDownloader.csproj"
CONTROLLER = PLUGIN_DIR / "Controllers" / "JellyfinDownloaderController.cs"

# csproj：`<EmbeddedResource Include="Web/inject_01_core.js" />`，文档顺序即声明顺序。
_EMBEDDED_FRAGMENT = re.compile(r'<EmbeddedResource\s+Include="(Web/inject[^"]*\.js)"\s*/>')
_ROOT_NAMESPACE = re.compile(r"<RootNamespace>([^<]+)</RootNamespace>")
# controller：资源名前缀/后缀常量与 `ScriptSegments = { "01_core", ... };` 顺序表。
_SEGMENT_PREFIX = re.compile(r'ScriptSegmentPrefix\s*=\s*"([^"]+)"')
_SEGMENT_SUFFIX = re.compile(r'ScriptSegmentSuffix\s*=\s*"([^"]+)"')
_SEGMENT_ORDER = re.compile(r"ScriptSegments\s*=\s*\{(.*?)\};", re.S)
_SEGMENT_NAME = re.compile(r'"([^"]+)"')


def _single(pattern: re.Pattern[str], text: str, subject: str) -> str:
    """取恰好一处匹配。守卫自己不能因为解析不到而静默变绿，所以找不到就直接失败。"""
    found = pattern.findall(text)
    if len(found) != 1:
        raise AssertionError(f"{subject}：期望恰好 1 处，实际 {len(found)} 处")
    return found[0]


def declared_fragment_paths() -> list[str]:
    """csproj 里声明为 EmbeddedResource 的分片（仓库相对路径），按声明顺序。"""
    return _EMBEDDED_FRAGMENT.findall(CSPROJ.read_text(encoding="utf-8"))


def declaration_resource_names() -> list[str]:
    """上者按 MSBuild 规则算出的资源名：RootNamespace + 相对路径，'/' 换成 '.'。"""
    csproj = CSPROJ.read_text(encoding="utf-8")
    root = _single(_ROOT_NAMESPACE, csproj, "csproj 的 RootNamespace")
    return [root + "." + path.replace("/", ".") for path in declared_fragment_paths()]


def controller_resource_names() -> list[str]:
    """controller 真正会去读的资源名，按 ScriptSegments 的顺序。"""
    source = CONTROLLER.read_text(encoding="utf-8")
    prefix = _single(_SEGMENT_PREFIX, source, "controller 的 ScriptSegmentPrefix")
    suffix = _single(_SEGMENT_SUFFIX, source, "controller 的 ScriptSegmentSuffix")
    block = _single(_SEGMENT_ORDER, source, "controller 的 ScriptSegments")
    segments = _SEGMENT_NAME.findall(block)
    if not segments:
        raise AssertionError("controller 的 ScriptSegments 是空的")
    return [prefix + segment + suffix for segment in segments]


class InjectFragmentsMatchTheAssemblyOrder(unittest.TestCase):
    """Web/ 的文件、csproj 的声明、controller 的顺序表必须是同一份清单。"""

    def test_controller_order_equals_csproj_declaration_order(self) -> None:
        self.assertEqual(
            controller_resource_names(),
            declaration_resource_names(),
            "controller 的 ScriptSegments 与 csproj 的 EmbeddedResource 不一致："
            "顺序不同 = 浏览器里代码被拼错位，成员不同 = 服务端静默少发代码",
        )

    def test_web_dir_holds_exactly_the_declared_fragments(self) -> None:
        declared = {Path(path).name for path in declared_fragment_paths()}
        on_disk = {path.name for path in WEB_DIR.glob("inject*.js")}
        self.assertEqual(declared, on_disk, "Web/ 下的分片与 csproj 的声明对不上")

    def test_monolithic_inject_js_is_gone(self) -> None:
        self.assertFalse(
            (WEB_DIR / "inject.js").exists(),
            "分片已取代 Web/inject.js，两者不能并存",
        )
