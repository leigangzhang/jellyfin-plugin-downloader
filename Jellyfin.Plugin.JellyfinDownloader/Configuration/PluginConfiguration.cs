using MediaBrowser.Model.Plugins;

namespace Jellyfin.Plugin.JellyfinDownloader.Configuration;

/// <summary>Plugin settings.</summary>
public class PluginConfiguration : BasePluginConfiguration
{
    /// <summary>Gets or sets the minimum score shown in the panel.</summary>
    public double MinScore { get; set; } = 60;

    /// <summary>Gets or sets the local search/scoring backend base URL.</summary>
    public string BackendBaseUrl { get; set; } = "http://127.0.0.1:8123";

    /// <summary>
    /// Gets or sets the Python interpreter used to run the backend.
    /// Empty means <c>python3</c> from PATH.
    /// </summary>
    public string BackendPython { get; set; } = string.Empty;

    /// <summary>Gets or sets the backend entry script; empty means the bundled backend/console_server.py.</summary>
    public string BackendScript { get; set; } = string.Empty;

    /// <summary>Gets or sets the backend port.</summary>
    public int BackendPort { get; set; } = 8123;

    /// <summary>
    /// Gets or sets the aria2c executable. Empty means "let ToolLocator decide":
    /// the path recorded by install.sh, then PATH, then the common prefixes.
    /// <c>aria2c</c> is what the backend uses to fetch torrent metadata.
    /// </summary>
    public string BackendAria2c { get; set; } = string.Empty;

    /// <summary>
    /// Gets or sets the ffprobe executable. Empty means "let ToolLocator decide"。
    /// 后端用它读媒体的元信息（时长、流、字幕轨）。
    /// </summary>
    public string BackendFfprobe { get; set; } = string.Empty;

    /// <summary>
    /// Gets or sets the ffmpeg executable. Empty means "let ToolLocator decide"。
    /// 后端用它做整段解码体检。
    /// </summary>
    public string BackendFfmpeg { get; set; } = string.Empty;

    /// <summary>
    /// Gets or sets the staging directory the downloader writes into before
    /// finalizing. Empty means the same-volume default: the sibling
    /// <c>.staging</c> directory of the media root.
    /// </summary>
    public string StagingRoot { get; set; } = string.Empty;

    /// <summary>
    /// Gets or sets the media library root (the directory that contains
    /// <c>Movies</c> / <c>TV Shows</c> / <c>Shows</c> / <c>Records</c>).
    /// Empty means "resolve it from the Jellyfin library configuration".
    /// </summary>
    public string MediaRoot { get; set; } = string.Empty;

    /// <summary>
    /// Gets the BT resource stations the backend searches. Empty means the
    /// backend's built-in defaults (the two stations this plugin shipped with);
    /// the defaults, the accepted <c>Kind</c> values and the validation live in
    /// <c>backend/source_config.py</c>.
    /// </summary>
    public List<BtSiteConfig> BtSites { get; set; } = new();

    /// <summary>
    /// Gets the L1 keyword combinations. Each entry is appended to the film title
    /// and asked before every other query tier, in order. Empty means the
    /// backend's built-in defaults.
    /// </summary>
    public List<string> L1Keywords { get; set; } = new();

    /// <summary>
    /// Gets or sets the TMDb API key used by the 「热门」 entry (the poster wall).
    /// Applied for at <c>https://www.themoviedb.org/settings/api</c>. Default is
    /// empty = 未配置：热门页只报「未配置」，不回退到任何内置 key。
    /// 这个值**只存在本机插件配置里**，配置页的 GET config 只回
    /// <c>tmdbApiKeyConfigured</c> 布尔，绝不下发 key 本身；保存空串 = 清除。
    /// </summary>
    public string TmdbApiKey { get; set; } = string.Empty;

    /// <summary>
    /// Gets or sets the TMDb language tag for the trending lists (default
    /// <c>zh-CN</c>：榜单按简体中文标题/简介返回)。空串在保存时回落到这个默认值。
    /// </summary>
    public string TmdbLanguage { get; set; } = DefaultTmdbLanguage;

    /// <summary>
    /// Gets the built-in trending list ids (TMDb list keys). 顺序即配置页复选框的
    /// 展示顺序：本周热门电影 / 本周热门剧集 / 流行电影 / 流行剧集。它是
    /// <see cref="TrendingLists"/> 留空时的默认值，也是配置页那四个复选框的唯一出处
    /// （页面不得另抄一份，见 tests/backend_retired/test_backend_retired_trending_anchor_naming.py）。
    /// </summary>
    public static readonly string[] DefaultTrendingLists =
    {
        "trending_movie_week",
        "trending_tv_week",
        "popular_movie",
        "popular_tv",
    };

    /// <summary>Gets the默认 TMDb 语言标签（配置页的 placeholder 与保存时的回落值）。</summary>
    public const string DefaultTmdbLanguage = "zh-CN";

    /// <summary>
    /// Gets or sets a value indicating whether关注条目时在媒体库写一个占位文件，
    /// 让条目先在 Jellyfin 里可见（默认 <c>false</c> = 只记录关注，对媒体库零写入）。
    /// 占位文件名带 <c>- zz placeholder</c> 标签，永远不等于后端归档的真文件落点。
    /// </summary>
    public bool AnchorEnabled { get; set; }

    /// <summary>
    /// Gets or sets the trending list ids the 「热门」 page shows. Empty means
    /// <see cref="DefaultTrendingLists"/> (保存时会归一到那份默认，而不是留空)。
    /// </summary>
    public List<string> TrendingLists { get; set; } = new(DefaultTrendingLists);
}
