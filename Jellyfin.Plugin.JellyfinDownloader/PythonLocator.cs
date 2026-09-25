using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;

namespace Jellyfin.Plugin.JellyfinDownloader;

/// <summary>
/// 探测用户系统上的 python3（后端脚本只需要标准库，3.9+ 即可）。
///
/// 顺序：配置项（若填了且可用）→ PATH 上的 python3/python → 常见安装位置
/// （Homebrew / 官方框架 / pyenv / conda / uv）。结果缓存 30 秒，
/// 避免配置页每次刷新都去起进程问版本。
/// </summary>
public static class PythonLocator
{
    private const int MinMajor = 3;
    private const int MinMinor = 9;

    private static readonly object Gate = new();
    private static readonly TimeSpan CacheTtl = TimeSpan.FromSeconds(30);
    private static DateTime _cachedAt = DateTime.MinValue;
    private static string _cachedKey = "\u0000";
    private static Candidate? _cached;

    /// <summary>探测结果。</summary>
    public sealed class Candidate
    {
        /// <summary>Gets 可执行文件路径（PATH 命中时就是命令名）。</summary>
        public string Path { get; init; } = string.Empty;

        /// <summary>Gets 版本号，如 <c>3.14.6</c>；空 = 没探到。</summary>
        public string Version { get; init; } = string.Empty;

        /// <summary>Gets 来源说明（配置页指定 / PATH / 常见位置 / 未找到）。</summary>
        public string Source { get; init; } = string.Empty;

        /// <summary>Gets 是否满足最低版本要求。</summary>
        public bool Ok { get; init; }

        /// <summary>Gets 是否找到了可执行文件（不论版本）。</summary>
        public bool Found => Path.Length > 0 && !string.Equals(Source, "未找到", StringComparison.Ordinal);
    }

    /// <summary>探测系统 python3（带 30 秒缓存）。</summary>
    public static Candidate Detect(string? configured)
    {
        var key = (configured ?? string.Empty).Trim();
        lock (Gate)
        {
            if (_cached is not null && _cachedKey == key && DateTime.UtcNow - _cachedAt < CacheTtl)
            {
                return _cached;
            }
        }

        var result = Probe(key);
        lock (Gate)
        {
            _cached = result;
            _cachedKey = key;
            _cachedAt = DateTime.UtcNow;
        }

        return result;
    }

    /// <summary>给启动后端用：优先配置项，其次探测结果，最后退回 PATH 上的 python3。</summary>
    public static string ResolveExecutable(string? configured)
    {
        var candidate = Detect(configured);
        if (candidate.Found && candidate.Ok)
        {
            return candidate.Path;
        }

        return string.IsNullOrWhiteSpace(configured) ? "python3" : configured!.Trim();
    }

    /// <summary>清空缓存（装完 Python 后强制重新探测）。</summary>
    public static void Invalidate()
    {
        lock (Gate)
        {
            _cachedAt = DateTime.MinValue;
        }
    }

    private static Candidate Probe(string configured)
    {
        var probes = new List<(string? Path, string Source, string Version)>();
        foreach (var (path, source) in Candidates(configured))
        {
            var version = VersionOf(path);
            if (version is not null)
            {
                probes.Add((path, source, version));
            }
        }

        // 配置页显式指定的：先看它；版本不达标也照样返回，让配置页给出提示
        var configuredProbe = probes.FirstOrDefault(p => p.Source == "配置页指定");
        if (configuredProbe.Path is not null)
        {
            return new Candidate
            {
                Path = configuredProbe.Path,
                Version = configuredProbe.Version,
                Source = configuredProbe.Source,
                Ok = IsSupported(configuredProbe.Version),
            };
        }

        // 自动探测：在满足最低版本的候选里选版本最高的（本机可能同时装了
        // 系统 3.9 与 pyenv 3.14，要挑后者）
        var best = probes
            .Where(p => IsSupported(p.Version))
            .OrderByDescending(p => VersionRank(p.Version))
            .FirstOrDefault();
        if (best.Path is not null)
        {
            return new Candidate { Path = best.Path!, Version = best.Version, Source = best.Source, Ok = true };
        }

        // 找到了解释器但版本都太低 → 展示一个，Ok=false，配置页会露出「安装 Python」
        if (probes.Count > 0)
        {
            var any = probes[0];
            return new Candidate { Path = any.Path!, Version = any.Version, Source = any.Source, Ok = false };
        }

        return new Candidate
        {
            Path = configured.Length > 0 ? configured : string.Empty,
            Version = string.Empty,
            Source = "未找到",
            Ok = false,
        };
    }

    private static int VersionRank(string version)
    {
        var parts = version.Split('.');
        if (parts.Length < 3
            || !int.TryParse(parts[0], out var major)
            || !int.TryParse(parts[1], out var minor)
            || !int.TryParse(parts[2], out var patch))
        {
            return 0;
        }

        return (major * 1_000_000) + (minor * 1_000) + patch;
    }

    private static IEnumerable<(string Path, string Source)> Candidates(string configured)
    {
        var seen = new HashSet<string>(StringComparer.Ordinal);

        if (configured.Length > 0)
        {
            seen.Add(configured);
            yield return (configured, "配置页指定");
        }

        foreach (var name in new[] { "python3", "python" })
        {
            if (seen.Add(name))
            {
                yield return (name, "PATH");
            }
        }

        var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var fixedPaths = new List<string>
        {
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3",
        };
        if (home.Length > 0)
        {
            fixedPaths.Add(Path.Combine(home, ".pyenv", "shims", "python3"));
            fixedPaths.Add(Path.Combine(home, ".local", "bin", "python3"));
            fixedPaths.Add(Path.Combine(home, "miniconda3", "bin", "python3"));
            fixedPaths.Add(Path.Combine(home, "anaconda3", "bin", "python3"));
        }

        foreach (var path in fixedPaths)
        {
            if (seen.Add(path))
            {
                yield return (path, "常见位置");
            }
        }

        foreach (var path in Globbed(home))
        {
            if (seen.Add(path))
            {
                yield return (path, "常见位置");
            }
        }
    }

    private static IEnumerable<string> Globbed(string home)
    {
        var patterns = new List<string>
        {
            "/Library/Frameworks/Python.framework/Versions/*/bin/python3",
        };
        if (home.Length > 0)
        {
            patterns.Add(Path.Combine(home, ".pyenv", "versions", "*", "bin", "python3"));
            patterns.Add(Path.Combine(home, ".local", "share", "uv", "python", "*", "bin", "python3"));
        }

        var results = new List<string>();
        foreach (var pattern in patterns)
        {
            var dir = Path.GetDirectoryName(pattern);
            var leaf = Path.GetFileName(pattern);
            while (dir is not null && dir.Contains('*', StringComparison.Ordinal))
            {
                dir = Path.GetDirectoryName(dir);
            }

            if (dir is null || !Directory.Exists(dir))
            {
                continue;
            }

            try
            {
                results.AddRange(Directory.EnumerateFiles(dir, leaf, SearchOption.AllDirectories));
            }
            catch (Exception)
            {
                // 权限/竞态问题直接跳过
            }
        }

        // 版本号大的优先（3.14 > 3.12），同版本按路径稳定排序
        return results.OrderByDescending(p => p).Take(8);
    }

    private static string? VersionOf(string executable)
    {
        try
        {
            var info = new ProcessStartInfo
            {
                FileName = executable,
                Arguments = "-V",
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true,
            };

            using var process = Process.Start(info);
            if (process is null)
            {
                return null;
            }

            var output = process.StandardOutput.ReadToEnd() + process.StandardError.ReadToEnd();
            if (!process.WaitForExit(3000))
            {
                try
                {
                    process.Kill(true);
                }
                catch (Exception)
                {
                    // ignore
                }

                return null;
            }

            var match = Regex.Match(output, @"Python\s+(\d+)\.(\d+)\.(\d+)");
            return match.Success ? $"{match.Groups[1].Value}.{match.Groups[2].Value}.{match.Groups[3].Value}" : null;
        }
        catch (Exception)
        {
            // 不存在 / 不可执行
            return null;
        }
    }

    private static bool IsSupported(string version)
    {
        var parts = version.Split('.');
        if (parts.Length < 2 || !int.TryParse(parts[0], out var major) || !int.TryParse(parts[1], out var minor))
        {
            return false;
        }

        return major > MinMajor || (major == MinMajor && minor >= MinMinor);
    }
}
