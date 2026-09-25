using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MediaBrowser.Controller.Library;

namespace Jellyfin.Plugin.JellyfinDownloader;

/// <summary>
/// 解析后端要用的落点路径。**源码里不含任何机器相关路径**，取值优先级：
///
/// 1. 插件配置页「媒体目录」/「暂存目录」手填的值；
/// 2. `媒体目录` 未填时，从 Jellyfin 媒体库配置推导：各库路径的公共父目录
///    （例如库是 `&lt;根&gt;/Movies`、`&lt;根&gt;/TV Shows`，则根就是那个公共父目录）；
/// 3. 都没拿到时用用户主目录下的占位默认（`~/Media`、`~/Downloads/.staging`）。
///
/// 暂存区的占位默认遵循「同盘暂存」惯例：库根的兄弟目录 `.staging`，
/// 这样归档能用同盘 `mv` 原子完成。
/// </summary>
public static class MediaPaths
{
    private static readonly string[] CategoryDirs = { "Movies", "TV Shows", "Shows", "Records" };

    private static readonly HashSet<string> ForbiddenRoots = new(StringComparer.Ordinal)
    {
        "/", "/Volumes", "/Users", "/private", "/System", "/Applications", "/tmp",
    };

    /// <summary>Gets 当前生效的媒体库根目录。</summary>
    public static string MediaRoot { get; private set; } = PlaceholderMediaRoot();

    /// <summary>Gets 该值的来源说明（给配置页显示）。</summary>
    public static string Source { get; private set; } = "占位默认";

    /// <summary>Gets 最近一次从 Jellyfin 读到的库路径。</summary>
    public static IReadOnlyList<string> Locations { get; private set; } = Array.Empty<string>();

    /// <summary>占位默认库根（~ 下的 Media）：只在未配置且解析不到 Jellyfin 库时使用。</summary>
    public static string PlaceholderMediaRoot()
    {
        return Path.Combine(UserHome(), "Media");
    }

    /// <summary>
    /// 暂存区占位默认：库根的兄弟目录 `.staging`（同盘，归档可原子 mv）；
    /// 库根层级太浅（父目录本身就是卷根，如 /srv/media）时退回 `~/Downloads/.staging`。
    /// </summary>
    public static string PlaceholderStagingRoot(string mediaRoot)
    {
        var parent = Path.GetDirectoryName(Path.GetFullPath(mediaRoot));
        if (!string.IsNullOrEmpty(parent) && Segments(parent) >= 2)
        {
            return Path.Combine(parent, ".staging");
        }

        return Path.Combine(UserHome(), "Downloads", ".staging");
    }

    /// <summary>按配置与 Jellyfin 媒体库刷新缓存（配置页读写、启动后端前调用）。</summary>
    public static void Refresh(ILibraryManager? library, string? configuredRoot = null)
    {
        var configured = (configuredRoot ?? string.Empty).Trim();
        if (configured.Length > 0)
        {
            MediaRoot = Path.GetFullPath(configured).TrimEnd('/');
            Source = "配置页指定";
            return;
        }

        try
        {
            if (library is not null)
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
            }
        }
        catch (Exception)
        {
            // 读库失败不影响插件：落到占位默认，配置页会说明来源
        }

        MediaRoot = PlaceholderMediaRoot();
        Source = "占位默认（未配置且未解析到媒体库）";
    }

    /// <summary>暂存区：配置页指定优先，否则按同盘惯例推导。</summary>
    public static string ResolveStagingRoot(string? configured, string? mediaRoot = null)
    {
        var value = (configured ?? string.Empty).Trim();
        if (value.Length > 0)
        {
            return Path.GetFullPath(value).TrimEnd('/');
        }

        return PlaceholderStagingRoot(string.IsNullOrWhiteSpace(mediaRoot) ? MediaRoot : mediaRoot!);
    }

    private static string UserHome()
    {
        var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        return string.IsNullOrEmpty(home) ? "/tmp" : home;
    }

    private static int Segments(string path)
    {
        return path.Split('/', StringSplitOptions.RemoveEmptyEntries).Length;
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

        // 只有一个库时公共父目录就是库本身（比如 <根>/Movies），那是分类目录不是库根，
        // 得再往上一层，否则会拼成 Movies/Movies/...
        while (paths.Contains(candidate) && candidate.Length > 1)
        {
            candidate = Path.GetDirectoryName(candidate)?.TrimEnd('/') ?? "/";
        }

        return candidate.Length > 1 ? candidate : null;
    }

    private static bool IsUsable(string candidate)
    {
        if (ForbiddenRoots.Contains(candidate) || Segments(candidate) < 2)
        {
            return false;
        }

        if (!Directory.Exists(candidate))
        {
            return false;
        }

        // 至少要有一个已知分类目录，否则说明该库布局跟后端约定对不上，宁可退回占位默认
        return CategoryDirs.Any(dir => Directory.Exists(Path.Combine(candidate, dir)));
    }
}
