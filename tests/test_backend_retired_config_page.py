"""配置页源码守卫：`configPage.html` 的行、布局、配色与元素 id。

Subject: `Configuration/configPage.html`（这些事实起 Jellyfin 才看得见，所以用
源码断言钉住）。
"""

from __future__ import annotations

import re
import unittest

from tests._backend_retired_helpers import BACKEND, ROOT, source_config


class ConfigPageGuardTests(unittest.TestCase):
    """`configPage.html` 的源码守卫：布局、状态徽标配色、元素 id 与文案。

    配置页只能靠源码断言守护（起 Jellyfin 才看得见的东西不在这里测），所以每条
    都用 `assertIn` / `assertNotIn` 钉住页面里的一段字面量。
    """

    def test_the_removed_rows_stay_removed(self):
        """按要求删掉的三行不许再回来：解析路径回显、暂存目录回显、后端运行状态行。

        用户的判断是它们把页面撑松了；这三行也是「用一次就过时」的信息。
        解析出来的媒体库路径仍以 placeholder 的形式保留（留空时的提示），
        后端状态改成只在出错时用 Dashboard.alert 报。
        """
        page = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "Configuration" / "configPage.html").read_text(encoding="utf-8")
        for element_id in ("jdl-media-resolved", "jdl-staging", "jdl-backend-status"):
            self.assertNotIn('id="%s"' % element_id, page, "已删除的行不该再出现：" + element_id)
        # 解析结果仍然要有去处：placeholder 里带上解析到的路径
        self.assertIn("media.placeholder = resolved", page)
        # 出错反馈不能因此消失
        self.assertIn("Dashboard.alert", page)

    def test_the_port_row_still_reports_a_stopped_backend(self):
        """服务端口第二行：端口值 + 运行状态 + 两个按钮；状态不许带 pid。

        原来那行「正在运行 · pid …」被删掉之后，运行状态改成了这行里的徽标
        （运行中 / 已停止 / 启动中）。使用方明确要求不带 pid 这类冗余字段，
        所以页面里不许再渲染它。标题自成一节（第一行），说明是第三行。
        """
        page = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "Configuration" / "configPage.html").read_text(encoding="utf-8")
        self.assertIn("status.running === false", page)
        self.assertIn("后端未运行", page)
        # 端口被顶替的说明也还在（同一个说明位的另一档）
        self.assertIn("已被占用，实际监听", page)
        # 三种状态文案都要在
        for state in ("运行中", "已停止", "启动中"):
            self.assertIn(state, page, f"状态徽标缺少「{state}」")
        # 第二行里：端口值、状态徽标、两个按钮
        row = page[page.index('class="jdl-port-row"'):]
        row = row[:row.index("jdl-port-hint")]
        for token in ("jdl-port-value", "jdl-status",
                      "jdl-backend-refresh", "jdl-backend-restart"):
            self.assertIn(token, row, f"服务端口状态行里缺少 {token}")
        # 标题在状态行之外，且是独立的一节
        self.assertIn('<h2 id="jdl-port-label" class="sectionTitle">服务端口</h2>', page)
        self.assertNotIn("服务端口</h2>", row)
        # 不渲染 pid 等冗余字段
        self.assertNotIn("status.pid", page, "运行状态不许带 pid")

    def test_status_badge_palette_passes_wcag_aa(self):
        """状态徽标的配色必须过 WCAG AA（白字对底色 ≥ 4.5:1）。

        只给文字着色的方案在浅色主题下不达标（绿 3.43:1、琥珀 2.45:1），所以徽标改成
        实心胶囊 + 白字——这样对比度由胶囊自身保证，明暗主题下都成立。这条守卫把
        「过 AA」从口头承诺变成会红的检查：改配色改到不达标就失败。
        """
        page = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "Configuration" / "configPage.html").read_text(encoding="utf-8")
        palette = dict(re.findall(r"\.jdl-status--(\w+) \{ background: (#[0-9a-fA-F]{6}); \}", page))
        self.assertEqual(set(palette), {"running", "stopped", "starting", "unknown"},
                         f"四种状态的底色都要定义：{sorted(palette)}")

        def luminance(color: str) -> float:
            channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                      for c in channels]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        for state, background in sorted(palette.items()):
            light, dark = luminance("#ffffff"), luminance(background)
            ratio = (max(light, dark) + 0.05) / (min(light, dark) + 0.05)
            self.assertGreaterEqual(round(ratio, 2), 4.5,
                                    f"{state} 的白字对比度只有 {ratio:.2f}:1，不到 AA")
        # 别退回「只给文字着色」的老方案
        self.assertNotIn("jdl-status--running { color:", page)

    def test_list_layout_keeps_inputs_and_button_columns_aligned(self):
        """列表的排布要求（都能在源码里查，避免改样式时悄悄破坏）：

        (a) 列表要清掉主题塞进来的内缩——否则站点行与媒体目录输入框差 20px；
        (b) 「等长」的范围是**站点与关键词这两组列表内部**（实测 627.5）：
            按钮区固定宽度，否则最后一行多一个 `+` 就比别的行窄、上移/下移/删除
            三列也会错开。**媒体目录不在这个范围内**——它没有按钮区，本来就该是
            满宽（实测 803.5），别把它也改成 627.5；
        (c) `+` 单独占第四列并往右挪一点；
        (d) 服务端口那两个按钮要压到和该行文字同一字号。
        """
        page = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "Configuration" / "configPage.html").read_text(encoding="utf-8")

        list_block = page[page.index(".jdl-list {"):]
        list_block = list_block[:list_block.index(".jdl-list__row")]
        self.assertIn("padding: 0", list_block, "列表必须清掉主题的内缩")
        self.assertIn("margin: 0", list_block)

        # 媒体目录输入框要和列表里的输入框等长：宽度必须由同一套变量算出来
        # （--jdl-actions-width / --jdl-row-gap），不能写死一个数字——写死就会在
        # 按钮区宽度变化时与列表漂移。
        self.assertIn("--jdl-actions-width:", page, "按钮区宽度要做成共享变量")
        self.assertIn("--jdl-row-gap:", page, "行间距要做成共享变量")
        media_rule = page[page.index("#jdl-media {"):]
        media_rule = media_rule[:media_rule.index("}")]
        self.assertIn("calc(100% - var(--jdl-actions-width) - var(--jdl-row-gap))",
                      media_rule, "媒体目录宽度必须与列表用同一套变量算")
        buttons_uses_var = page[page.index(".jdl-list__buttons {"):]
        buttons_uses_var = buttons_uses_var[:buttons_uses_var.index("}")]
        self.assertIn("flex: 0 0 var(--jdl-actions-width)", buttons_uses_var,
                      "按钮区宽度必须引用同一个变量，否则两处会漂移")
        media_tag = page[page.index('id="jdl-media"'):]
        media_tag = media_tag[:media_tag.index(">")]
        self.assertNotIn("style=", media_tag, "媒体目录输入框不该有内联宽度")

        buttons_block = page[page.index(".jdl-list__buttons {"):]
        buttons_block = buttons_block[:buttons_block.index("}")]
        self.assertIn("flex: 0 0 var(--jdl-actions-width)", buttons_block,
                      "按钮区必须定宽，否则输入框长度会随按钮个数变化")
        self.assertNotIn("flex: 0 0 auto", buttons_block)

        add_rule = page[page.index('[data-action="add"]'):]
        add_rule = add_rule[:add_rule.index("}")]
        self.assertIn("margin-left", add_rule, "新增 + 要往右挪，和本行三个操作分开")

        # 图标按钮必须清掉主题的外边距：主题给每个按钮每边约 6.3px，
        # 4 个叠起来会把预留区顶大、把最后一行输入框挤窄（实测窄 24px）。
        icon_btn = page[page.index(".jdl-icon-btn {"):]
        icon_btn = icon_btn[:icon_btn.index("}")]
        self.assertIn("margin: 0", icon_btn, "图标按钮必须清掉主题外边距")

        # 服务端口要跟另外三组同构：标题独占一行（h2.sectionTitle），第二行才是
        # 端口值 + 状态 + 按钮，第三行是说明。字号靠「同一个元素 + 同一个类」保证，
        # 不抄 1.5em 这种数字——另一套主题的 h2 是 1.8em。
        self.assertIn('<h2 id="jdl-port-label" class="sectionTitle">服务端口</h2>',
                      page, "端口标题要用与其它小节标题相同的元素与类，并独占一行")
        self.assertNotIn(".jdl-port-row__label", page, "标题搬出去后不该再留行内标签的样式")
        port_section = page[page.index('<h2 id="jdl-port-label"'):]
        port_section = port_section[:port_section.index("jdl-port-hint")]
        title_at = port_section.index('id="jdl-port-label"')
        row_at = port_section.index('class="jdl-port-row"')
        self.assertLess(title_at, row_at, "标题必须排在状态行之前")
        self.assertNotIn("服务端口</h2>", port_section[row_at:], "状态行里不该再有标题文本")

        port_btn = page[page.index(".jdl-port-btn {"):]
        port_btn = port_btn[:port_btn.index("}")]
        self.assertIn("font-size: 1em", port_btn, "端口按钮字号要与该行文字一致")
        self.assertIn("jdl-port-btn", page)
        for button_id in ("jdl-backend-refresh", "jdl-backend-restart"):
            self.assertIn(button_id, page)

    def test_station_rows_keep_their_live_hint_slot(self):
        """站点行的实时识别提示不能只在渲染时非空才创建。

        用户把一个已知站点改成认不出的地址时，得有地方实时显示「未能识别」；
        元素只在渲染时非空才建的话，这一路反馈就没了（保存仍会被拒，但用户看不到原因）。
        """
        page = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "Configuration" / "configPage.html").read_text(encoding="utf-8")
        self.assertNotIn("if (siteHintFor(item.address, item.kind)) {", page,
                         "说明元素必须无条件创建，空的时候隐藏")
        self.assertIn("row._field.caption = hint;", page)

    def test_placeholders_follow_the_current_defaults(self):
        """输入框的示例文案要跟当前默认值一致，否则用户看到的「默认」是过期的。"""
        page = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "Configuration" / "configPage.html").read_text(encoding="utf-8")
        self.assertIn("如：高清剧集网", page)
        import source_config

        self.assertIn(source_config.DEFAULT_ANCHOR_KEYWORDS[0], page,
                      "示例文案要取默认里的第一个关键词")

    def test_keyword_defaults_are_one_word_per_row(self):
        """默认关键词必须一条一个词——使用方拍板「不要多词组合」。

        依据是 2026-10-14 的实测：该索引对一行里的多个词是 AND 语义，混进一个不在
        索引里的词（例如 BBHDTV）会让整行 0 命中；拆成单行则只有那一行空转。
        这条同时钉住 C# 与 Python 两侧的默认值形态。
        """
        import source_config

        for keyword in source_config.DEFAULT_ANCHOR_KEYWORDS:
            self.assertNotIn(" ", keyword,
                             f"默认关键词 {keyword!r} 是多个词，会触发 AND 语义的整行空转")

        controller = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "Controllers"
                      / "JellyfinDownloaderController.cs").read_text(encoding="utf-8")
        keywords_block = controller[controller.index("DefaultKeywords"):]
        cs_keywords = re.findall(r'"([^"]+)"', keywords_block[:keywords_block.index("};")])
        self.assertEqual(cs_keywords, list(source_config.DEFAULT_ANCHOR_KEYWORDS))
        for keyword in cs_keywords:
            self.assertNotIn(" ", keyword, f"C# 默认关键词 {keyword!r} 也是多词组合")

    def test_the_keyword_default_documents_its_measured_caveat(self):
        """BBHDTV 是使用方指定的值，而实测 0 命中——这个矛盾必须写在代码里。

        不然下一个人读注释只看「实测命中率」，会以为默认值是挑过的最优解；
        或者反过来把使用方要的值当成笔误直接删掉。
        """
        backend = (BACKEND / "source_config.py").read_text(encoding="utf-8")
        self.assertIn("BBHDTV", backend)
        self.assertIn("BTHDTV", backend, "实测有效的那个词也要留痕")
        # 逐词实测那一行（BBHDTV 0 命中）必须留着
        self.assertIn("BBHDTV`      -> 0 命中", backend, "逐词实测备注必须留着")
        # 默认值里出现 BBHDTV 这件事要有一句「使用方的决定」说明
        self.assertIn("使用方拍板", backend)
        # 旧话术不能再回来：默认值不是「实测有效的那两条」了。这句是复核逐字点出来的矛盾。
        self.assertNotIn("实测有效的那两条", backend,
                         "默认值早就不是「实测有效的那两条」，别再这么写")
        # 2026-10-14 复测出来的 AND 语义与「整行 0 命中」这个事实必须留在代码里：
        # 它是「一条一个词」这条默认形态的唯一依据，丢了就等于没理由。
        self.assertIn("AND 语义", backend)
        self.assertIn("BBHDTV`       -> **0 条**", backend)
        # 2026-10-14 复测出来的 AND 语义与「整条 0 命中」这个事实必须留在代码里：
        # 它是「为什么不能把不确定的词塞进同一条组合」的唯一依据。
        self.assertIn("AND 语义", backend)
        self.assertIn("BBHDTV`       -> **0 条**", backend)

    def test_the_config_page_keeps_the_element_ids_the_script_binds_to(self):
        """配置页的 id 是脚本与 DOM 之间的契约：改掉一个 id 就静默失去交互。"""
        page = (ROOT / "Jellyfin.Plugin.JellyfinDownloader" / "Configuration" / "configPage.html").read_text(encoding="utf-8")
        for element_id in ("jdl-media", "jdl-sites", "jdl-keywords", "jdl-port-value",
                           "jdl-backend-refresh", "jdl-backend-restart", "jdl-save"):
            self.assertIn('id="%s"' % element_id, page, "配置页缺少 %s" % element_id)
        # 一个站一个输入框：旧的四字段行（名称/类型/接口/网页）与暂存目录输入框不该留下
        self.assertNotIn('data-field="Kind"', page)
        self.assertNotIn('id="jdl-staging-input"', page)
        # 上移 / 下移 / 删除 / 新增四种按钮都要能被 syncList 认出来
        for action in ("'up'", "'down'", "'remove'", "'add'"):
            self.assertIn(action, page)
        self.assertIn("data-action", page)


if __name__ == "__main__":
    unittest.main()
