using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using Jellyfin.Data.Enums;
using Jellyfin.Plugin.JellyfinDownloader.Configuration;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;

namespace Jellyfin.Plugin.JellyfinDownloader.Controllers;

/// <summary>Serves injected assets and proxies to the local search backend.</summary>
[ApiController]
[Route("JellyfinDownloader")]
public class JellyfinDownloaderController : ControllerBase
{
    // Web/inject.js 已按职责拆成这些分片：前缀 + 段名 + 后缀 = 资源名。
    private const string ScriptSegmentPrefix = "Jellyfin.Plugin.JellyfinDownloader.Web.inject_";
    private const string ScriptSegmentSuffix = ".js";
    private const string StyleResource = "Jellyfin.Plugin.JellyfinDownloader.Web.style.css";
    private static readonly HttpClient Client = new() { Timeout = TimeSpan.FromMinutes(30) };
    private static readonly object PackageGate = new();
    private static byte[]? _packageZip;
    private static string? _packageChecksum;
    private static readonly string[] AllowedPrefixes =
    {
        "search", "snapshot", "submit", "watch", "mark", "cleanup", "pan/",
        // 热门：/api/trending（TMDb 榜单代理）与 /api/trending/watch（关注）都走后端。
        "trending",
    };

    /// <summary>
    /// 内置默认资源站与默认关键词，配置为空时由配置页显示。站点这里只放**站点本身**：
    /// 完整接口路径（/api/search、/service/search）在保存时由 <see cref="NormaliseSite"/>
    /// 拼接并落盘，用户在页面上看到的始终是站名。这两组默认必须与
    /// <c>backend/source_config.py</c> 的 <c>DEFAULT_SITES</c> / <c>DEFAULT_ANCHOR_KEYWORDS</c>
    /// 指向同一批站点与关键词（两边各有守卫盯着）。
    /// </summary>
    private static readonly string[] DefaultSiteUrls =
    {
        "https://m.diao.im",
        "https://torrents-csv.com",
    };

    private static readonly string[] DefaultKeywords = { "高清剧集网", "DDHDTV", "BBHDTV" };

    // 「关注」占位文件的命名（相对媒体根）：{0} = 清洗后的片名，{1} = 年份段。
    //   movie / record → <分类目录>/<safeTitle><year>/<safeTitle><year> - zz placeholder.mp4
    //   series        → TV Shows/<safeTitle><year>/Season 01/<safeTitle> S01E01 - …mp4
    // 分类目录随归一后的落点类别走：movie → Movies，series → TV Shows，record → Records
    //（纪录片与电影同形：一个目录 + 一部片，所以 record 复用 movie 的文件名形状）。
    // PlaceholderLabel 是占位与后端归档真文件之间**唯一**的区分（同名同目录，只差这一段），
    // 少了它就会撞名、静默丢文件。守卫逐条对照两边落点，见
    // tests/backend_retired/test_backend_retired_trending_anchor_naming.py。
    private const string PlaceholderLabel = " - zz placeholder";
    private const string PlaceholderExtension = ".mp4";

    /// <summary>年份段模板（<c>{0}</c> = 4 位年份）：目录与文件名共用它。</summary>
    private const string YearSegmentTemplate = " ({0})";

    private static readonly string MovieRelativeTemplate =
        "Movies/{0}{1}/{0}{1}" + PlaceholderLabel + PlaceholderExtension;

    private static readonly string SeriesRelativeTemplate =
        "TV Shows/{0}{1}/Season 01/{0} S01E01" + PlaceholderLabel + PlaceholderExtension;

    private static readonly string RecordRelativeTemplate =
        "Records/{0}{1}/{0}{1}" + PlaceholderLabel + PlaceholderExtension;

    /// <summary>
    /// 没有 TMDb key 时下发给 UI 的中文引导（单一事实源）：配置页与热门面板都用这一句。
    /// 面板优先读 <c>config.guidance</c>，它自己那句硬编码只是老版本兜底——文案只在这里维护，
    /// 免得两处各写一份漂移（review r1 的 F3）。后端 /api/trending 的 guidance 字段保留：
    /// 那是对直连 API 的消费者的契约，UI 不再依赖它。
    /// </summary>
    private const string TmdbGuidance = "尚未配置 TMDb API Key：在插件配置页的「热门」分组里填好后再打开本面板（榜单与海报都需要它）。";

    /// <summary>内嵌占位模板资源（<c>Web/placeholder.mp4</c>，csproj 登记）。</summary>
    private const string PlaceholderResource = "Jellyfin.Plugin.JellyfinDownloader.Web.placeholder.mp4";

    /// <summary>文件名里不许出现的字符：路径分隔符与保留字符。</summary>
    private const string ForbiddenTitleChars = "\\/:*?\"<>|";

    /// <summary>连续空白折叠成单个空格（片名里的换行不该进入文件名）。</summary>
    private const string WhitespaceRunPattern = "\\s+";

    /// <summary>年份必须是 4 位数字（「片名 (2024)」里的那个年份）。</summary>
    private const string FourDigitYearPattern = "^[0-9]{4}$";

    /// <summary>
    /// 片名归一化（查库匹配用）：小写 + 只留 ASCII 字母数字与 CJK。字符类与
    /// <c>backend/download/check_exists.py</c> 的 <c>CLEAN_RE</c> 逐字相同（守卫盯着），
    /// 但不剥年份——我们额外按 ProductionYear 精确比对，剥了会把「1917」这类片名抹平。
    /// </summary>
    private const string TitleCleanPattern = "[^a-z0-9\\u4e00-\\u9fff]";

    private static readonly Regex WhitespaceRunRegex = new(WhitespaceRunPattern, RegexOptions.Compiled);
    private static readonly Regex FourDigitYearRegex = new(FourDigitYearPattern, RegexOptions.Compiled);
    private static readonly Regex TitleCleanRegex = new(TitleCleanPattern, RegexOptions.Compiled);

    /// <summary>Gets the injected client script (fragments joined in <see cref="ScriptSegments"/> order).</summary>
    [HttpGet("script")]
    [AllowAnonymous]
    public IActionResult GetScript()
    {
        NoCache();
        return Content(ReadScript(), "application/javascript; charset=utf-8");
    }

    /// <summary>Gets the injected stylesheet.</summary>
    [HttpGet("style")]
    [AllowAnonymous]
    public IActionResult GetStyle()
    {
        NoCache();
        return Content(ReadResource(StyleResource), "text/css; charset=utf-8");
    }

    /// <summary>Gets the client-side configuration.</summary>
    [HttpGet("config")]
    [AllowAnonymous]
    public IActionResult GetConfig()
    {
        NoCache();
        RefreshMediaPaths();
        var config = Plugin.Instance?.Configuration;
        // 配置为空 = 用内置默认，所以页面上显示的也应该是内置默认本身：
        // 「一行都没有时用内置默认」这条对用户是隐形的，不该让他面对一个空输入框。
        var sites = config?.BtSites is { Count: > 0 }
            ? DescribeSites(config.BtSites)
            : DefaultSiteUrls
                .Select(url => (object)new { name = SiteHost(url, null), kind = KindForUrl(url),
                    baseUrl = url, webSearchUrl = string.Empty })
                .ToList();
        var keywords = config?.L1Keywords is { Count: > 0 }
            ? config.L1Keywords
            : DefaultKeywords.ToList();
        return new JsonResult(new
        {
            minScore = config?.MinScore ?? 60,
            StagingRoot = config?.StagingRoot ?? string.Empty,
            MediaRoot = config?.MediaRoot ?? string.Empty,
            BackendPython = config?.BackendPython ?? string.Empty,
            // 配置页一个站只显示一个地址：站点名按主机名给（后端也用同一规则）。
            // 这个对象里每个键只能出现一次（大小写不敏感也要算）：`btSites` 与
            // `BtSites`、`BackendPort` 与 `backendPort` 同时出现，System.Text.Json
            // 的冲突检测会直接抛异常，整个接口 500——配置页因此读不到任何东西。
            // 测试 tests/test_backend_retired_paths.py 的 json 键冲突守卫盯着这条。
            btSites = sites,
            l1Keywords = keywords,
            resolvedMediaRoot = MediaPaths.MediaRoot,
            // 生效端口与状态接口同一个事实源：端口被占用时这里是自增后的那个值。
            backendPort = BackendManager.EffectivePort,
            // 热门（TMDb）：只回「配没配」，绝不下发 key 本身（浏览器不需要它）。
            tmdbApiKeyConfigured = (config?.TmdbApiKey ?? string.Empty).Trim().Length > 0,
            // 无 key 时下发引导（面板据此直接渲染提示，不再打榜单接口）；有 key 时是 null，
            // 面板不能把 null/空当成「没配」。
            guidance = (config?.TmdbApiKey ?? string.Empty).Trim().Length > 0 ? null : TmdbGuidance,
            tmdbLanguage = LanguageOrDefault(config?.TmdbLanguage),
            // 回显生效的榜单清单：留空时就是内置默认那四个。
            trendingLists = TrendingListsOrDefault(config?.TrendingLists),
            anchorEnabled = config?.AnchorEnabled ?? false,
        });
    }

    /// <summary>Persists the editable config fields (port / staging directory).</summary>
    [HttpPost("config")]
    [Authorize(Policy = "RequiresElevation")]
    public IActionResult UpdateConfig([FromBody] ConfigUpdate body)
    {
        NoCache();
        var plugin = Plugin.Instance;
        var config = plugin?.Configuration;
        if (plugin is null || config is null)
        {
            return BadRequest(new { error = "插件未初始化" });
        }

        if (body.BackendPort is > 0)
        {
            config.BackendPort = body.BackendPort.Value;
        }

        if (body.StagingRoot is not null)
        {
            config.StagingRoot = body.StagingRoot.Trim();
        }

        if (body.MediaRoot is not null)
        {
            config.MediaRoot = body.MediaRoot.Trim();
        }

        if (body.BackendPython is not null)
        {
            config.BackendPython = body.BackendPython.Trim();
            PythonLocator.Invalidate();
        }

        if (body.BtSites is not null)
        {
            var error = ValidateBtSites(body.BtSites);
            if (error is not null)
            {
                return BadRequest(new { error });
            }

            config.BtSites = body.BtSites
                .Where(site => !string.IsNullOrWhiteSpace(site.BaseUrl)
                    || !string.IsNullOrWhiteSpace(site.WebSearchUrl))
                .Select(NormaliseSite)
                .ToList();
        }

        if (body.L1Keywords is not null)
        {
            config.L1Keywords = body.L1Keywords
                .Select(keyword => (keyword ?? string.Empty).Trim())
                .Where(keyword => keyword.Length > 0)
                .Distinct()
                .ToList();
        }

        // 热门（TMDb）：null 跳过；TmdbApiKey 允许空串 = 清除（配置页永远提交这个字段）。
        if (body.TmdbApiKey is not null)
        {
            config.TmdbApiKey = body.TmdbApiKey.Trim();
        }

        if (body.TmdbLanguage is not null)
        {
            config.TmdbLanguage = LanguageOrDefault(body.TmdbLanguage);
        }

        if (body.TrendingLists is not null)
        {
            // 空串过滤 + 去重；一个都不剩就回到内置四榜单（空列表 = 热门页一个都不显示）。
            var lists = body.TrendingLists
                .Select(item => (item ?? string.Empty).Trim())
                .Where(item => item.Length > 0)
                .Distinct(StringComparer.Ordinal)
                .ToList();
            config.TrendingLists = lists.Count > 0 ? lists : new List<string>(PluginConfiguration.DefaultTrendingLists);
        }

        if (body.AnchorEnabled is not null)
        {
            config.AnchorEnabled = body.AnchorEnabled.Value;
        }

        plugin.SaveConfiguration();

        // 这个页面上的每个字段最终都是后端**启动时**的参数：端口是命令行参数，
        // 媒体根 / 暂存区 / 站点清单 / L1 关键字都是 BackendManager.Start 写进去的环境
        // 变量。所以保存后一律重启后端，配置页那行「保存后会自动重启后端」说的就是这件事。
        var restart = BackendManager.Restart();

        return new JsonResult(new
        {
            ok = true,
            backendRestarted = restart.Ok,
            backendDetail = restart.Detail,
            StagingRoot = config.StagingRoot,
            MediaRoot = config.MediaRoot,
            BackendPython = config.BackendPython,
            btSites = DescribeSites(config.BtSites),
            l1Keywords = config.L1Keywords,
            // 重启后真正监听的那个端口：被占用时它是自增后的值。
            backendPort = BackendManager.EffectivePort,
            // 与 GET config 同一套语义：key 只回「配没配」，不回值。
            tmdbApiKeyConfigured = config.TmdbApiKey.Trim().Length > 0,
            tmdbLanguage = LanguageOrDefault(config.TmdbLanguage),
            trendingLists = TrendingListsOrDefault(config.TrendingLists),
            anchorEnabled = config.AnchorEnabled,
        });
    }

    /// <summary>TMDb 语言标签：空串（或全空白）回落到内置默认 <c>zh-CN</c>。</summary>
    private static string LanguageOrDefault(string? language)
    {
        var value = (language ?? string.Empty).Trim();
        return value.Length > 0 ? value : PluginConfiguration.DefaultTmdbLanguage;
    }

    /// <summary>生效的榜单清单：空列表（或全空白项）回落到内置四榜单。</summary>
    private static List<string> TrendingListsOrDefault(List<string>? lists)
    {
        var cleaned = (lists ?? new List<string>())
            .Select(item => (item ?? string.Empty).Trim())
            .Where(item => item.Length > 0)
            .Distinct(StringComparer.Ordinal)
            .ToList();
        return cleaned.Count > 0 ? cleaned : new List<string>(PluginConfiguration.DefaultTrendingLists);
    }

    /// <summary>
    /// 配置页一个站只填一个地址：这里把地址判定成磁力接口或云盘搜索页。
    /// 判定必须与 <c>backend/source_config.py</c> 的 classify_site_url 一致，
    /// 否则保存下去的 kind 会与后端解析结果对不上。
    /// </summary>
    private static BtSiteConfig NormaliseSite(ConfigSiteDto site)
    {
        var raw = (site.BaseUrl ?? string.Empty).Trim();
        var declaredKind = (site.Kind ?? string.Empty).Trim().ToLowerInvariant();
        var webSearchUrl = (site.WebSearchUrl ?? string.Empty).Trim();

        if (raw.Length > 0 && raw.Contains("{q}", StringComparison.Ordinal))
        {
            webSearchUrl = webSearchUrl.Length > 0 ? webSearchUrl : raw;
            raw = string.Empty;
        }

        var kind = SiteKindForUrl(raw, declaredKind);
        if (raw.Length > 0 && kind == "diao" && UrlPath(raw).Length <= 1)
        {
            raw = raw.TrimEnd('/') + "/api/search";
        }
        else if (raw.Length > 0 && kind == "torrents-csv" && UrlPath(raw).Length <= 1)
        {
            raw = raw.TrimEnd('/') + "/service/search";
        }

        return new BtSiteConfig
        {
            Name = SiteHost(raw.Length > 0 ? raw : webSearchUrl, site.Name),
            // 云盘专用站（没有接口地址）没有可解析的响应，类型归一化成空。
            Kind = raw.Length == 0 ? string.Empty : kind,
            BaseUrl = raw,
            WebSearchUrl = webSearchUrl,
        };
    }

    private static string SiteKindForUrl(string url, string declaredKind)
    {
        // 判定不出来的地址保留调用方的声明；两者都没有就沿用改动前的默认 diao。
        // **保存前一定会先过 ValidateBtSites**：配置页认不出来的地址在那里就被拒了，
        // 走不到这个兜底；只有手改过 XML 的老站点才靠它兼容。
        var kind = KindForUrl(url);
        if (kind.Length > 0)
        {
            return kind;
        }

        return declaredKind.Length > 0 ? declaredKind : "diao";
    }

    /// <summary>地址 → 适配器类型；认不出来的主机返回空串（由调用方决定拒绝还是兜底）。</summary>
    private static string KindForUrl(string url)
    {
        var host = SiteHostName(url);
        if (host is "m.diao.im" or "diao.im")
        {
            return "diao";
        }

        if (host is "torrents-csv.com" or "www.torrents-csv.com")
        {
            return "torrents-csv";
        }

        return string.Empty;
    }

    private static string UrlPath(string url)
    {
        return Uri.TryCreate(url, UriKind.Absolute, out var parsed) ? parsed.AbsolutePath : string.Empty;
    }

    private static string SiteHostName(string url)
    {
        return Uri.TryCreate(url, UriKind.Absolute, out var parsed) ? parsed.Host.ToLowerInvariant() : string.Empty;
    }

    private static string SiteHost(string url, string? declaredName)
    {
        var host = SiteHostName(url);
        if (host is "m.diao.im" or "diao.im")
        {
            return "diao.im";
        }

        if (host is "torrents-csv.com" or "www.torrents-csv.com")
        {
            return "torrents-csv";
        }

        return host.Length > 0 ? host : (declaredName ?? string.Empty).Trim();
    }

    /// <summary>配置页回显：站点名按主机名给，地址照原样显示。</summary>
    private static List<object> DescribeSites(List<BtSiteConfig>? sites)
    {
        return (sites ?? new List<BtSiteConfig>())
            .Select(site => (object)new
            {
                name = SiteHost((site.BaseUrl ?? string.Empty).Trim().Length > 0
                        ? site.BaseUrl!.Trim()
                        : (site.WebSearchUrl ?? string.Empty).Trim(), site.Name),
                kind = site.Kind ?? string.Empty,
                baseUrl = site.BaseUrl ?? string.Empty,
                webSearchUrl = site.WebSearchUrl ?? string.Empty,
            })
            .ToList();
    }

    /// <summary>Lists the seasons of a series (server-side, no client API guessing).</summary>
    [HttpGet("seasons")]
    [Authorize(Policy = "RequiresElevation")]
    public IActionResult GetSeasons([FromQuery] string seriesId)
    {
        NoCache();
        var empty = new JsonResult(Array.Empty<object>());
        if (string.IsNullOrWhiteSpace(seriesId))
        {
            return empty;
        }

        if (!Guid.TryParseExact(seriesId, "N", out var id) && !Guid.TryParse(seriesId, out id))
        {
            return empty;
        }

        var library = HttpContext.RequestServices.GetService(typeof(ILibraryManager)) as ILibraryManager;
        if (library is null)
        {
            return empty;
        }

        var series = library.GetItemById(id);
        if (series is null)
        {
            return empty;
        }

        var query = new InternalItemsQuery
        {
            ParentId = series.Id,
            Recursive = false,
            IncludeItemTypes = new[] { BaseItemKind.Season },
        };

        var seasons = library.GetItemList(query)
            .Where(item => item.IndexNumber.HasValue)
            .OrderBy(item => item.IndexNumber!.Value)
            .Select(item => new
            {
                index = item.IndexNumber,
                name = item.Name,
                id = item.Id.ToString("N"),
            })
            .ToArray();

        return new JsonResult(seasons);
    }

    /// <summary>
    /// 「热门」页的批量查库：按 标题+年份 回答条目是否已在媒体库里，只回真假、不写任何
    /// 东西。请求 {"items":[{"title","year","kind"}]}，响应同序同长（回显的 kind 是归一
    /// 后的 movie/series）；items 缺失/非法（不是数组、某项缺 title/kind 或类别/年份不合法）
    /// 时整批回 {"items":[]}，不半答。
    ///
    /// **没有年份就不断言 exists**：一律回 false。这是有意的保守策略——TMDb 缺年份的老片
    /// 很常见，按标题匹配会把「同名不同年」的库内条目判成命中，而命中会让卡片显示「已在库」
    /// 并禁用「关注」按钮（用户就下不了片了）；假阴性只是卡片显示成未入库，危害小得多。
    /// 守卫：tests/backend_retired/test_backend_retired_trending_anchor_naming.py 里钉住了这条。
    /// </summary>
    [HttpPost("trending/library-status")]
    [Authorize(Policy = "RequiresElevation")]
    public IActionResult TrendingLibraryStatus([FromBody] TrendingStatusRequest? body)
    {
        NoCache();
        var queries = ReadTrendingQueries(body);
        if (queries is null)
        {
            return new JsonResult(new { items = Array.Empty<object>() });
        }

        var library = HttpContext.RequestServices.GetService(typeof(ILibraryManager)) as ILibraryManager;
        var known = library is null ? new List<(string Kind, string Title, int? Year)>() : LoadKnownTitles(library);
        var items = queries
            .Select(query => (object)new
            {
                title = query.Title,
                year = query.Year,
                kind = query.Kind,
                exists = query.Year.HasValue
                    && known.Any(item => query.LibraryKinds.Contains(item.Kind, StringComparer.Ordinal)
                        && item.Title == query.Normalized
                        && item.Year == query.Year.Value),
            })
            .ToList();

        return new JsonResult(new { items });
    }

    /// <summary>
    /// 「关注」时在媒体库里写一个占位文件，让条目先在 Jellyfin 里可见（管理员端点）。
    /// AnchorEnabled = false 时**零写入**（不解析媒体根、不建目录、不落文件）回 disabled。
    /// 类别接受平台词表（movie | series | episode | variety | record）并归一到落点类别，
    /// 决定落 Movies / TV Shows / Records；认不出的类别回 400 invalid kind。
    /// **必须有 4 位年份**，缺失回 400 year is required：无年份落点是 <title>/，与真条目的
    /// <title> (<year>)/ 永不相等，必然多造一个重复条目（正是本提案要防的库污染）。
    /// </summary>
    [HttpPost("trending/anchor")]
    [Authorize(Policy = "RequiresElevation")]
    public IActionResult TrendingAnchor([FromBody] TrendingAnchorRequest? body)
    {
        NoCache();
        if (body is null
            || !TryReadStringValue(body.Title, out var title)
            || !TryReadStringValue(body.Kind, out var kind)
            || !TryReadYearValue(body.Year, out var year))
        {
            return BadRequest(new { error = "invalid title or year" });
        }

        if (title.Length == 0)
        {
            return BadRequest(new { error = "title is required" });
        }

        // 类别归一后再判：面板发的类别来自 TMDb 的口径（剧集是 episode/tv），不归一的话
        // 剧集卡片点「关注」会被判成非法 kind → 占位永不落盘（review r1 的 F1）。
        var normalizedKind = NormalizeKind(kind);
        if (normalizedKind is null)
        {
            return BadRequest(new { error = "invalid kind" });
        }

        // 年份是落点的一部分，不能缺：缺了会落到 <title>/，与真条目 <title> (<year>)/ 不相等。
        if (!year.HasValue)
        {
            return BadRequest(new { error = "year is required" });
        }

        var yearText = year.Value.ToString(CultureInfo.InvariantCulture);
        var safeTitle = CleanTitle(title);
        if (safeTitle.Length == 0
            || title.Contains("..", StringComparison.Ordinal)
            || !FourDigitYearRegex.IsMatch(yearText))
        {
            return BadRequest(new { error = "invalid title or year" });
        }

        var config = Plugin.Instance?.Configuration;
        if (config is null || !config.AnchorEnabled)
        {
            // 开关关着 = 只记录关注，对媒体库零写入。这里连 MediaPaths 都不刷新。
            return new JsonResult(new { anchored = false, exists = false, reason = "disabled" });
        }

        // 媒体根是静态缓存（平时由配置页/启动后端刷新），这条链路要自己刷。
        RefreshMediaPaths();
        var root = Path.GetFullPath(MediaPaths.MediaRoot);
        var relative = PlaceholderRelativePath(safeTitle, yearText, normalizedKind);
        var finalPath = Path.GetFullPath(Path.Combine(root, relative));
        if (!IsWithinRoot(finalPath, root))
        {
            return BadRequest(new { error = "invalid title or year" });
        }

        if (System.IO.File.Exists(finalPath))
        {
            // 重复 anchor（或上一次已经写成功）：**不重写**。重写只搅动 mtime，
            // 对 Jellyfin 没有任何好处，还会把并发写入变成非幂等。
            return new JsonResult(new { anchored = false, exists = true, path = finalPath });
        }

        var bytes = ReadPlaceholderBytes();
        if (bytes is null || bytes.Length == 0)
        {
            // 读不到模板就响亮失败：0 字节的 mp4 会被 Jellyfin 当坏媒体扫进去。
            return StatusCode(
                StatusCodes.Status500InternalServerError,
                new { error = "placeholder resource missing" });
        }

        var directory = Path.GetDirectoryName(finalPath);
        if (string.IsNullOrEmpty(directory))
        {
            return BadRequest(new { error = "invalid title or year" });
        }

        // 先写同目录临时文件再原子改名：Jellyfin 的扫描器永远不会看到半写好的文件。
        var temporary = finalPath + ".tmp-" + Guid.NewGuid().ToString("N");
        try
        {
            System.IO.Directory.CreateDirectory(directory);
            System.IO.File.WriteAllBytes(temporary, bytes);
            System.IO.File.Move(temporary, finalPath);
        }
        catch (Exception ex)
        {
            TryDelete(temporary);
            if (System.IO.File.Exists(finalPath))
            {
                // 并发重复 anchor：另一次写赢了，结果一样。
                return new JsonResult(new { anchored = false, exists = true, path = finalPath });
            }

            return StatusCode(
                StatusCodes.Status500InternalServerError,
                new { error = "anchor failed: " + ex.Message });
        }

        return new JsonResult(new { anchored = true, exists = false, path = finalPath });
    }

    /// <summary>
    /// 解析 {"items":[…]}：标题/类别必须是非空字符串（类别只认 movie|series），年份要么
    /// 缺失/空串、要么是能解析成整数的数字（`"2024"` 与 `2024` 都收）。非法返回 null。
    /// </summary>
    private static List<(string Title, int? Year, string Kind, string[] LibraryKinds, string Normalized)>?
        ReadTrendingQueries(TrendingStatusRequest? body)
    {
        if (body?.Items is not { } items || items.ValueKind != JsonValueKind.Array)
        {
            return null;
        }

        var queries = new List<(string Title, int? Year, string Kind, string[] LibraryKinds, string Normalized)>();
        foreach (var element in items.EnumerateArray())
        {
            if (element.ValueKind != JsonValueKind.Object)
            {
                return null;
            }

            if (!TryGetField(element, "title", out var titleField)
                || !TryReadStringValue(titleField, out var title)
                || title.Length == 0)
            {
                return null;
            }

            if (!TryGetField(element, "kind", out var kindField)
                || !TryReadStringValue(kindField, out var kind)
                || kind.Length == 0)
            {
                return null;
            }

            // 与 anchor 同一套词汇归一（面板可能发 episode/tv），但**缺 kind 仍然非法**：
            // 查库得知道该跟 Movie 还是 Series 比。回显的是归一后的值。
            var matchKind = NormalizeKind(kind);
            if (matchKind is null)
            {
                return null;
            }

            // 年份是**可选**的：字段缺失（或 null/空串）就是「没有年份」，不算非法。
            int? year = null;
            if (TryGetField(element, "year", out var yearField) && !TryReadYearValue(yearField, out year))
            {
                return null;
            }

            queries.Add((title, year, matchKind, LibraryKindsFor(matchKind), NormalizeTitle(title)));
        }

        return queries;
    }

    /// <summary>
    /// 库里的 Movie / Series 两类条目（Recursive = true）。库很小，一次取回在内存里匹配。
    /// </summary>
    private static List<(string Kind, string Title, int? Year)> LoadKnownTitles(ILibraryManager library)
    {
        var known = new List<(string Kind, string Title, int? Year)>();
        foreach (var (kind, itemKind) in new[] { ("movie", BaseItemKind.Movie), ("series", BaseItemKind.Series) })
        {
            var query = new InternalItemsQuery
            {
                Recursive = true,
                IncludeItemTypes = new[] { itemKind },
            };

            foreach (var item in library.GetItemList(query))
            {
                var title = NormalizeTitle(item.Name);
                if (title.Length > 0)
                {
                    known.Add((kind, title, item.ProductionYear));
                }
            }
        }

        return known;
    }

    /// <summary>取字段（先精确、再忽略大小写）；字段不存在时 value = null。</summary>
    private static bool TryGetField(JsonElement element, string name, out JsonElement? value)
    {
        value = null;
        if (element.ValueKind != JsonValueKind.Object)
        {
            return false;
        }

        if (element.TryGetProperty(name, out var exact))
        {
            value = exact;
            return true;
        }

        foreach (var property in element.EnumerateObject())
        {
            if (string.Equals(property.Name, name, StringComparison.OrdinalIgnoreCase))
            {
                value = property.Value;
                return true;
            }
        }

        return false;
    }

    /// <summary>可缺省的 JSON 值 → 字符串：缺失 / null = 空串；非字符串 = 失败。</summary>
    private static bool TryReadStringValue(JsonElement? property, out string value)
    {
        value = string.Empty;
        if (property is not { } element || element.ValueKind == JsonValueKind.Null)
        {
            return true;
        }

        if (element.ValueKind != JsonValueKind.String)
        {
            return false;
        }

        value = (element.GetString() ?? string.Empty).Trim();
        return true;
    }

    /// <summary>
    /// JSON 值 → 年份：缺失/null/空串 = 无年份；数字或数字串 = 有年份；其余 = 失败。
    /// </summary>
    private static bool TryReadYearValue(JsonElement? property, out int? year)
    {
        year = null;
        if (property is not { } element || element.ValueKind == JsonValueKind.Null)
        {
            return true;
        }

        switch (element.ValueKind)
        {
            case JsonValueKind.Number:
                if (!element.TryGetInt32(out var number))
                {
                    return false;
                }

                year = number;
                return true;
            case JsonValueKind.String:
                var text = (element.GetString() ?? string.Empty).Trim();
                if (text.Length == 0)
                {
                    return true;
                }

                if (int.TryParse(text, NumberStyles.None, CultureInfo.InvariantCulture, out var parsed))
                {
                    year = parsed;
                    return true;
                }

                return false;
            default:
                return false;
        }
    }

    /// <summary>
    /// 片名清洗：剥掉路径分隔符与文件名字符、折叠连续空白、去首尾空白。纯函数，A7 守卫
    /// 直接读这里的常量与函数体做断言。
    /// </summary>
    private static string CleanTitle(string title)
    {
        var stripped = new StringBuilder(title.Length);
        foreach (var character in title)
        {
            if (ForbiddenTitleChars.IndexOf(character, StringComparison.Ordinal) >= 0)
            {
                continue;
            }

            stripped.Append(character);
        }

        return WhitespaceRunRegex.Replace(stripped.ToString(), " ").Trim();
    }

    /// <summary>
    /// 占位文件的相对路径（相对 <see cref="MediaPaths.MediaRoot"/>）。纯函数，模板/标签/
    /// 年份段都是显式常量，守卫按名字读出来逐条对照后端落点。<paramref name="kind"/> 是
    /// <see cref="NormalizeKind"/> 归一后的落点类别（movie / series / record）。
    /// </summary>
    private static string PlaceholderRelativePath(string safeTitle, string year, string kind)
    {
        var segment = year.Length > 0
            ? string.Format(CultureInfo.InvariantCulture, YearSegmentTemplate, year)
            : string.Empty;
        var template = kind switch
        {
            "series" => SeriesRelativeTemplate,
            "record" => RecordRelativeTemplate,
            _ => MovieRelativeTemplate,
        };
        return string.Format(CultureInfo.InvariantCulture, template, safeTitle, segment);
    }

    /// <summary>拼接后的路径必须真的落在媒体根之内（越界一律拒，不写库外）。</summary>
    private static bool IsWithinRoot(string path, string root)
    {
        var normalizedRoot = root.TrimEnd(Path.DirectorySeparatorChar);
        return path.Length > normalizedRoot.Length
            && path.StartsWith(normalizedRoot + Path.DirectorySeparatorChar, StringComparison.Ordinal);
    }

    /// <summary>读内嵌的占位模板字节；读不到返回 null（调用方回 500）。</summary>
    private static byte[]? ReadPlaceholderBytes()
    {
        using var stream = typeof(Plugin).Assembly.GetManifestResourceStream(PlaceholderResource);
        if (stream is null)
        {
            return null;
        }

        using var buffer = new MemoryStream();
        stream.CopyTo(buffer);
        return buffer.ToArray();
    }

    private static void TryDelete(string path)
    {
        try
        {
            if (System.IO.File.Exists(path))
            {
                System.IO.File.Delete(path);
            }
        }
        catch (Exception)
        {
            // 清理失败不影响结论：.tmp-<guid> 后缀不会被 Jellyfin 扫成媒体。
        }
    }

    /// <summary>
    /// 类别归一：把三套口径折算成插件自己的**落点类别** movie / series / record。
    /// 面板发的是后端 VALID_KINDS（movie | episode | variety | record），TMDb 说 tv，
    /// 库里说 Series——端点只认归一后的值，不再只认 movie|series：不归一的话，剧集
    /// 「关注」会被判成非法 kind，占位永不落盘（review r1 的 F1 就是这条跨层分叉）。
    /// 归一是端点自己的责任，不要求客户端二次翻译。
    ///   movie | film            → movie   （Movies/，与电影同形）
    ///   series | episode | tv | show | variety → series（TV Shows/Season 01/，剧集形状）
    ///   record | records        → record  （Records/，与电影同形）
    /// 空串按 movie 处理（老调用方不带类别时的既有行为）；认不出的类别返回 null，
    /// 由调用方决定拒绝还是整批回空。
    /// </summary>
    private static string? NormalizeKind(string? kind)
    {
        return (kind ?? string.Empty).Trim().ToLowerInvariant() switch
        {
            "" or "movie" or "film" => "movie",
            "series" or "episode" or "tv" or "show" or "variety" => "series",
            "record" or "records" => "record",
            _ => null,
        };
    }

    /// <summary>
    /// 查库时该跟哪些库内类别比：电影只比 Movie，剧集/综艺只比 Series；纪录片（record）
    /// 两种形态在 Jellyfin 里都可能存在（单集纪录片像 Movie、系列纪录片像 Series），
    /// 所以它同时比这两类——Records 只是媒体根下的分类目录，不是库内条目类型。
    /// </summary>
    private static string[] LibraryKindsFor(string kind)
    {
        return kind switch
        {
            "series" => new[] { "series" },
            "record" => new[] { "movie", "series" },
            _ => new[] { "movie" },
        };
    }

    /// <summary>片名归一化（查库匹配用）：见 <see cref="TitleCleanPattern"/> 的说明。</summary>
    private static string NormalizeTitle(string? title)
    {
        return TitleCleanRegex.Replace((title ?? string.Empty).ToLowerInvariant(), string.Empty);
    }

    /// <summary>Self-hosted plugin manifest so Jellyfin can resolve /Packages/{name}.</summary>
    [HttpGet("manifest.json")]
    [AllowAnonymous]
    public IActionResult GetManifest()
    {
        NoCache();
        MarkManifestFetch();
        BuildPackage();

        var id = (Plugin.Instance?.Id ?? Guid.Empty).ToString();
        var version = typeof(Plugin).Assembly.GetName().Version?.ToString() ?? "1.0.0.0";
        var baseUrl = $"{Request.Scheme}://{Request.Host}";
        var manifest = new object[]
        {
            new
            {
                guid = id,
                name = Plugin.Instance?.Name ?? "Jellyfin Downloader",
                description = "在电影 / 剧集 / 单集详情页提供「获取资源」入口（仅管理员）。",
                overview = "详情页「获取资源」：搜索源、打分，列出 >=60 分候选。",
                owner = "ray",
                category = "General",
                imageUrl = $"{baseUrl}/Plugins/{id}/{version}/Image",
                versions = new object[]
                {
                    new
                    {
                        version,
                        changelog = "初始版本：详情页「获取资源」入口，搜索+打分后列出 >=60 分候选。",
                        targetAbi = "10.11.11.0",
                        sourceUrl = $"{baseUrl}/JellyfinDownloader/package.zip",
                        checksum = _packageChecksum ?? string.Empty,
                        timestamp = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ"),
                    },
                },
            },
        };

        return new JsonResult(manifest);
    }

    /// <summary>Serves the installable plugin package (zip of the plugin folder).</summary>
    [HttpGet("package.zip")]
    [AllowAnonymous]
    public IActionResult GetPackage()
    {
        NoCache();
        return File(BuildPackage(), "application/zip", "JellyfinDownloader_1.0.0.0.zip");
    }

    /// <summary>Gets the local backend status.</summary>
    [HttpGet("backend/status")]
    [Authorize(Policy = "RequiresElevation")]
    public IActionResult BackendStatus()
    {
        NoCache();
        RefreshMediaPaths();
        return new JsonResult(BackendManager.Status());
    }

    /// <summary>Starts the local backend.</summary>
    [HttpPost("backend/start")]
    [Authorize(Policy = "RequiresElevation")]
    public IActionResult BackendStart()
    {
        NoCache();
        RefreshMediaPaths();
        var result = BackendManager.Start();
        return new JsonResult(new { ok = result.Ok, detail = result.Detail, status = BackendManager.Status() });
    }

    /// <summary>Stops the local backend.</summary>
    [HttpPost("backend/stop")]
    [Authorize(Policy = "RequiresElevation")]
    public IActionResult BackendStop()
    {
        NoCache();
        var result = BackendManager.Stop();
        return new JsonResult(new { ok = result.Ok, detail = result.Detail, status = BackendManager.Status() });
    }

    /// <summary>Restarts the local backend (also replaces a stale/external one).</summary>
    [HttpPost("backend/restart")]
    [Authorize(Policy = "RequiresElevation")]
    public IActionResult BackendRestart()
    {
        NoCache();
        RefreshMediaPaths();
        var result = BackendManager.Restart();
        return new JsonResult(new { ok = result.Ok, detail = result.Detail, status = BackendManager.Status() });
    }

    /// <summary>Re-reads the Jellyfin library paths so the backend gets a fresh MEDIA_ROOT.</summary>
    private void RefreshMediaPaths()
    {
        var library = HttpContext.RequestServices.GetService(typeof(ILibraryManager)) as ILibraryManager;
        MediaPaths.Refresh(library, Plugin.Instance?.Configuration?.MediaRoot);
    }

    /// <summary>Proxies whitelisted backend calls (admin only).</summary>
    [AcceptVerbs("GET", "POST")]
    [Route("api/{**path}")]
    [Authorize(Policy = "RequiresElevation")]
    public async Task Proxy(string path)
    {
        Response.ContentType = "application/json; charset=utf-8";

        if (!IsAllowed(path))
        {
            Response.StatusCode = StatusCodes.Status404NotFound;
            await Response.WriteAsync("{\"error\":\"path not allowed\"}").ConfigureAwait(false);
            return;
        }

        // 必须用 BackendManager.BaseUrl，不能读 config.BackendBaseUrl：端口被占用时会
        // 自增（8123 → 8124），而配置里那个字段全仓没有写入点、永远停在 8123。照它转发
        // 会让面板的 search/submit/watch/verify/mark/cleanup/pan 在自增端口下全部 502。
        var baseUrl = BackendManager.BaseUrl;
        var target = $"{baseUrl}/api/{path}{Request.QueryString.Value}";

        using var request = new HttpRequestMessage(new HttpMethod(Request.Method), target);
        if (HttpMethods.IsPost(Request.Method))
        {
            using var reader = new StreamReader(Request.Body, Encoding.UTF8);
            var body = await reader.ReadToEndAsync().ConfigureAwait(false);
            if (body.Length > 0)
            {
                request.Content = new StringContent(body, Encoding.UTF8, "application/json");
            }
        }

        try
        {
            using var response = await Client.SendAsync(request).ConfigureAwait(false);
            Response.StatusCode = (int)response.StatusCode;
            var content = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
            await Response.WriteAsync(content).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            Response.StatusCode = StatusCodes.Status502BadGateway;
            var message = ex.Message.Replace("\\", "/", StringComparison.Ordinal).Replace("\"", "'", StringComparison.Ordinal);
            await Response.WriteAsync($"{{\"error\":\"backend unreachable: {message}\"}}").ConfigureAwait(false);
        }
    }

    private static bool IsAllowed(string path)
    {
        if (string.IsNullOrEmpty(path))
        {
            return false;
        }

        foreach (var prefix in AllowedPrefixes)
        {
            if (path.Equals(prefix, StringComparison.OrdinalIgnoreCase)
                || path.StartsWith(prefix + "/", StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
        }

        return false;
    }

    private void NoCache()
    {
        Response.Headers.CacheControl = "no-store, no-cache, must-revalidate";
        Response.Headers.Pragma = "no-cache";
        Response.Headers.Expires = "0";
    }

    private static void MarkManifestFetch()
    {
        try
        {
            var root = Path.GetDirectoryName(typeof(Plugin).Assembly.Location) ?? ".";
            System.IO.File.AppendAllText(
                Path.Combine(root, "manifest-fetch.log"),
                DateTime.Now.ToString("yyyy-MM-ddTHH:mm:ss") + Environment.NewLine);
        }
        catch
        {
            // diagnostics only
        }
    }

    private static byte[] BuildPackage()
    {
        lock (PackageGate)
        {
            if (_packageZip is not null)
            {
                return _packageZip;
            }

            var root = Path.GetDirectoryName(typeof(Plugin).Assembly.Location) ?? ".";
            using var buffer = new MemoryStream();
            using (var zip = new ZipArchive(buffer, ZipArchiveMode.Create, true))
            {
                foreach (var file in Directory.GetFiles(root, "*", SearchOption.AllDirectories))
                {
                    var relative = Path.GetRelativePath(root, file).Replace('\\', '/');
                    if (relative.StartsWith("backend/state", StringComparison.OrdinalIgnoreCase)
                        || relative.Contains("__pycache__", StringComparison.OrdinalIgnoreCase)
                        || relative.EndsWith(".log", StringComparison.OrdinalIgnoreCase))
                    {
                        continue;
                    }

                    zip.CreateEntryFromFile(file, relative, CompressionLevel.Optimal);
                }
            }

            _packageZip = buffer.ToArray();
            using var md5 = MD5.Create();
            _packageChecksum = Convert.ToHexString(md5.ComputeHash(_packageZip)).ToLowerInvariant();
            return _packageZip;
        }
    }

    /// <summary>
    /// /script 输出由这些分片按此顺序拼成——这一列的顺序就是浏览器里的代码顺序。
    /// 新增 / 改名分片时三处必须同步：Web/ 下的文件、csproj 的 EmbeddedResource、
    /// 这份列表。<see cref="DescribeScriptSegmentMismatch"/> 拿程序集里真实存在的资源
    /// 反查这份列表，因为「加了分片却忘了登记」与服务端静默少发代码是同一件事。
    /// </summary>
    private static readonly string[] ScriptSegments =
    {
        "01_core",
        "02_entry",
        "03_panel",
        "04_search",
        "05_render",
        // 06_trending（「热门」页签 + 关注按钮）由同批的客户端改动提供：
        // Web/inject_06_trending.js 与 csproj 里那一行 EmbeddedResource 都在那个改动里，
        // 本清单按契约先登记（合并后三处一致，同批的 inject_parts 守卫盯着）。
        "06_trending",
    };

    private static string ReadResource(string name)
    {
        var assembly = typeof(Plugin).Assembly;
        using var stream = assembly.GetManifestResourceStream(name);
        if (stream is null)
        {
            return string.Empty;
        }

        using var reader = new StreamReader(stream, Encoding.UTF8);
        return reader.ReadToEnd();
    }

    /// <summary>
    /// 按 <see cref="ScriptSegments"/> 的顺序读分片并拼接。拼接结果必须与拆分前的
    /// Web/inject.js 逐字节相同：分片边界都落在顶层语句之间的整行上，拼接即还原原文件。
    /// </summary>
    private static string ReadScript()
    {
        var assembly = typeof(Plugin).Assembly;
        var mismatch = DescribeScriptSegmentMismatch(assembly.GetManifestResourceNames());
        if (mismatch is not null)
        {
            throw new InvalidOperationException(mismatch);
        }

        var script = new StringBuilder();
        foreach (var segment in ScriptSegments)
        {
            script.Append(ReadResource(SegmentResourceName(segment)));
        }

        return script.ToString();
    }

    /// <summary>
    /// 守卫：把装配顺序列表与程序集里真实存在的分片资源对账，不一致就返回要抛出的信息。
    /// 缺一个 = 顺序表里写了一个不存在的分片；多一个 = 新加了分片却忘了登记。两种都等于
    /// 服务端静默少发代码，所以宁可让 /script 响亮失败，也不发一份缺块的脚本给浏览器。
    /// 参数化成「资源名清单」是为了让这条判定可以被独立验证（见修复记录/审查命令）。
    /// </summary>
    private static string? DescribeScriptSegmentMismatch(IEnumerable<string> bundledResources)
    {
        var bundled = bundledResources.ToList();
        var declared = ScriptSegments.Select(SegmentResourceName).ToList();
        var missing = declared.Where(name => !bundled.Contains(name, StringComparer.Ordinal)).ToList();
        var unlisted = bundled
            .Where(name => name.StartsWith(ScriptSegmentPrefix, StringComparison.Ordinal)
                && name.EndsWith(ScriptSegmentSuffix, StringComparison.Ordinal)
                && !declared.Contains(name, StringComparer.Ordinal))
            .ToList();
        if (missing.Count == 0 && unlisted.Count == 0)
        {
            return null;
        }

        return "[JellyfinDownloader] inject.js 分片与 ScriptSegments 对不上：顺序表里缺失 ["
            + string.Join(", ", missing) + "]，未登记进顺序表 ["
            + string.Join(", ", unlisted) + "]。请同步 Web/ 下的分片、csproj 的 EmbeddedResource "
            + "与 JellyfinDownloaderController.ScriptSegments 三处后重新 ./build.sh。";
    }

    private static string SegmentResourceName(string segment)
    {
        return ScriptSegmentPrefix + segment + ScriptSegmentSuffix;
    }

    /// <summary>
    /// Shape check for the station list. Returns an error message, or null when the
    /// list may be saved. Only the shape is checked here: the backend
    /// (<c>backend/source_config.py</c>) owns the accepted kinds, the defaults and
    /// the size limits, and it rejects a bad list loudly instead of silently using
    /// the defaults.
    /// </summary>
    private static string? ValidateBtSites(List<ConfigSiteDto> sites)
    {
        for (var index = 0; index < sites.Count; index++)
        {
            var site = sites[index];
            var label = "第 " + (index + 1) + " 个资源站";
            var address = (site.BaseUrl ?? string.Empty).Trim();
            var web = (site.WebSearchUrl ?? string.Empty).Trim();
            var declaredKind = (site.Kind ?? string.Empty).Trim().ToLowerInvariant();

            // 一个站至少要有一个入口；只有网页搜索地址的站是合法的云盘专用站。
            if (address.Length == 0 && web.Length == 0)
            {
                return label + "：至少要填「接口地址」或「网页搜索地址」中的一个";
            }

            if (address.Length > 0)
            {
                if (!IsHttpUrl(address))
                {
                    return label + "：地址必须是 http(s) URL";
                }

                // 配置页一个站只填一个地址：地址带 {q} 就是云盘搜索页，不带就必须是
                // 认得出来的磁力接口。认不出来的地址要当场拒绝——直接兜成 diao 会让
                // 一个打不通的地址静默变成「diao 站」，用户只看到「搜了但没结果」。
                if (!address.Contains("{q}", StringComparison.Ordinal)
                    && KindForUrl(address).Length == 0
                    && declaredKind.Length == 0)
                {
                    return label + "：识别不出这个地址该走哪个入口，请填 m.diao.im / "
                        + "torrents-csv.com 的接口地址，或带 {q} 占位符的网页搜索地址";
                }
            }

            if (web.Length > 0 && (!IsHttpUrl(web) || !web.Contains("{q}", StringComparison.Ordinal)))
            {
                return label + "：网页搜索地址要么留空，要么是含 {q} 占位符的 http(s) URL";
            }
        }

        return null;
    }

    private static bool IsHttpUrl(string? value)
    {
        var url = (value ?? string.Empty).Trim();
        return url.StartsWith("http://", StringComparison.OrdinalIgnoreCase)
            || url.StartsWith("https://", StringComparison.OrdinalIgnoreCase);
    }
}

/// <summary>Payload for POST /JellyfinDownloader/config.</summary>
public class ConfigUpdate
{
    /// <summary>Gets or sets the backend port (optional).</summary>
    public int? BackendPort { get; set; }

    /// <summary>Gets or sets the staging directory (optional).</summary>
    public string? StagingRoot { get; set; }

    /// <summary>Gets or sets the media library root (optional).</summary>
    public string? MediaRoot { get; set; }

    /// <summary>Gets or sets the Python interpreter (optional; empty = auto-detect).</summary>
    public string? BackendPython { get; set; }

    /// <summary>Gets or sets the BT resource stations (optional; empty list = backend defaults).</summary>
    public List<ConfigSiteDto>? BtSites { get; set; }

    /// <summary>Gets or sets the L1 keyword combinations (optional; empty list = backend defaults).</summary>
    public List<string>? L1Keywords { get; set; }

    /// <summary>
    /// Gets or sets the TMDb API key (optional). 空串 = **清除**（配置页永远提交这个字段，
    /// 用户不改就是空串）；null = 本次请求没带，保持原值。
    /// </summary>
    public string? TmdbApiKey { get; set; }

    /// <summary>Gets or sets the TMDb language tag (optional; empty = 回到内置默认 zh-CN).</summary>
    public string? TmdbLanguage { get; set; }

    /// <summary>Gets or sets the trending list ids (optional; 空列表 = 回到内置四榜单).</summary>
    public List<string>? TrendingLists { get; set; }

    /// <summary>Gets or sets the library placeholder switch (optional; null = keep).</summary>
    public bool? AnchorEnabled { get; set; }
}

/// <summary>Payload for POST /JellyfinDownloader/trending/library-status; 形状不对由 action
/// 回 {"items":[]}，而不是让模型绑定抛异常变成一个 ProblemDetails 400。</summary>
public class TrendingStatusRequest
{
    /// <summary>Gets or sets the batch to look up: [{"title","year","kind"}].</summary>
    public JsonElement? Items { get; set; }
}

/// <summary>Payload for POST /JellyfinDownloader/trending/anchor: {"title","year","kind"}.</summary>
public class TrendingAnchorRequest
{
    /// <summary>Gets or sets the item title (required).</summary>
    public JsonElement? Title { get; set; }

    /// <summary>Gets or sets the release year (optional; 4 digits when given).</summary>
    public JsonElement? Year { get; set; }

    /// <summary>Gets or sets the kind: "movie" | "series" (optional; 缺省按 movie).</summary>
    public JsonElement? Kind { get; set; }
}

/// <summary>
/// 配置页 POST 上来的资源站。配置页一个站只有一个地址输入框，所以地址放在
/// <see cref="BaseUrl"/> 里；<c>Kind</c> / <c>WebSearchUrl</c> 只为兼容旧客户端保留。
/// </summary>
public class ConfigSiteDto
{
    /// <summary>Gets or sets the display name (optional; the host name is used when empty).</summary>
    public string? Name { get; set; }

    /// <summary>Gets or sets the adapter kind (optional; inferred from the address).</summary>
    public string? Kind { get; set; }

    /// <summary>Gets or sets the address the user typed.</summary>
    public string? BaseUrl { get; set; }

    /// <summary>Gets or sets a cloud search template (legacy clients only).</summary>
    public string? WebSearchUrl { get; set; }
}
