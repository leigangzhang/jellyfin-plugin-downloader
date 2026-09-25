using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MediaBrowser.Controller.Library;

namespace Jellyfin.Plugin.JellyfinDownloader;

/// <summary>
/// 从 Jellyfin 媒体库配置解析后端要用的路径。
///
/// 后端约定「落点 = MEDIA_ROOT/&lt;分类&gt;/片名 (年份)」，分类固定为
/// Movies / TV Shows / Shows / Records，所以这里把各库路径的公共父目录当作
/// MEDIA_ROOT（本机即 /Volumes/XIAOMI SSD2/Media）。取不到或不符合该约定时，
/// 退回后端内置默认值，行为与以前一致。
/// </summary>
public static class MediaPaths
{
    /// <summary>后端内置默认（仅兜底）。</summary>
    public const string FallbackMediaRoot = "/Volumes/XIAOMI SSD2/Media";

    /// <summary>后端内置默认暂存区（仅兜底）。</summary>
    public const string DefaultStagingRoot = "/Volumes/XIAOMI SSD2/.staging";

    private static readonly string[] CategoryDirs = { "Movies", "TV Shows", "Shows", "Records" };

    private static readonly HashSet<string> ForbiddenRoots = new(StringComparer.Ordinal)
    {
        "/", "/Volumes", "/Users", "/private", "/System", "/Applications",
    };

    /// <summary>Gets 解析出的媒体库根目录。</summary>
    public static string MediaRoot { get; private set; } = FallbackMediaRoot;

    /// <summary>Gets 该值的来源说明（给配置页显示）。</summary>
    public static string Source { get; private set; } = "内置默认";

    /// <summary>Gets 最近一次读到的库路径。</summary>
    public static IReadOnlyList<string> Locations { get; private set; } = Array.Empty<string>();

    /// <summary>按 Jellyfin 当前媒体库配置刷新缓存（在配置页状态/启动后端前调用）。</summary>
    public static void Refresh(ILibraryManager? library)
    {
        if (library is null)
        {
            return;
        }

        try
        {
            var locations = library.GetVirtualFolders()
                .SelectMany(folder => folder.Locations ?? Array.Empty<string>())
                .Where(path => !string.IsNullOrWhiteSpace(path))
                .Select(path => Path.GetFullPath(path).TrimEnd('/'))
                .Where(path => path.Length > 1)
                .Distinct(StringComparer.Ordinal)
                .ToList();

            Locations = locations;

            var candidate = CommonParent(locations);
            if (candidate is not null && IsUsable(candidate))
            {
                MediaRoot = candidate;
                Source = "Jellyfin 媒体库";
                return;
            }

            MediaRoot = FallbackMediaRoot;
            Source = locations.Count == 0
                ? "内置默认（未读到媒体库路径）"
                : "内置默认（库路径不符合 <根>/<分类> 约定）";
        }
        catch (Exception)
        {
            // 读库失败不该影响插件：保留上一次的结果
        }
    }

    /// <summary>暂存区：配置页指定优先，其次内置默认。</summary>
    public static string ResolveStagingRoot(string? configured)
    {
        return string.IsNullOrWhiteSpace(configured)
            ? DefaultStagingRoot
            : Path.GetFullPath(configured.Trim()).TrimEnd('/');
    }

    private static string? CommonParent(List<string> paths)
    {
        if (paths.Count == 0)
        {
            return null;
        }

        var common = paths[0].Split('/', StringSplitOptions.RemoveEmptyEntries).ToList();
        foreach (var path in paths.Skip(1))
        {
            var other = path.Split('/', StringSplitOptions.RemoveEmptyEntries);
            var keep = 0;
            while (keep < common.Count && keep < other.Length && common[keep] == other[keep])
            {
                keep++;
            }

            common = common.Take(keep).ToList();
        }

        if (common.Count == 0)
        {
            return null;
        }

        var candidate = "/" + string.Join('/', common);

        // 只有一个库时公共父目录就是库本身（比如 .../Media/Movies），那是分类目录
        // 不是库根，得再往上一层，否则会拼成 Movies/Movies/...
        while (paths.Contains(candidate) && candidate.Length > 1)
        {
            candidate = Path.GetDirectoryName(candidate)?.TrimEnd('/') ?? "/";
        }

        return candidate.Length > 1 ? candidate : null;
    }

    private static bool IsUsable(string candidate)
    {
        if (ForbiddenRoots.Contains(candidate) || candidate.Count(c => c == '/') < 2)
        {
            return false;
        }

        if (!Directory.Exists(candidate))
        {
            return false;
        }

        // 至少要有一个已知分类目录，否则说明该库布局跟后端约定对不上
        return CategoryDirs.Any(dir => Directory.Exists(Path.Combine(candidate, dir)));
    }
}
