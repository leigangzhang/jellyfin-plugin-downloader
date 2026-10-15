using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;

namespace Jellyfin.Plugin.JellyfinDownloader;

/// <summary>
/// 探测系统上的 aria2c / ffprobe / ffmpeg。
///
/// 顺序：配置项（若填了且可用）→ 安装期记录文件（<c>&lt;插件安装目录&gt;/backend-tools.json</c>，
/// 由 <c>install.sh</c> 写出）→ PATH → 常见安装位置（Homebrew / /usr/local / MacPorts）。
///
/// 为什么不能只靠 PATH：Jellyfin 从 Finder / <c>open</c> 启动时继承的是 launchd 的
/// 最小 PATH，里面没有 <c>/opt/homebrew/bin</c>，而后端是按裸名调用这三个工具的，
/// 于是「机器上没装」会被上游吞成「这个种子里没有可读视频」。
///
/// 与 <see cref="PythonLocator"/> 的分工不同：Python 需要起进程问版本（所以有 30 秒缓存），
/// 这里只做文件系统判断，每次现算——装完工具应当立刻生效，不留缓存窗口。
/// </summary>
public static class ToolLocator
{
    /// <summary>安装期写出的记录文件名，落在插件安装目录下（<c>install.sh</c> 写同一份）。</summary>
    public const string RecordFileName = "backend-tools.json";

    /// <summary>记录文件里那张「工具名 → 绝对路径」表的键名。</summary>
    private const string RecordToolsKey = "tools";

    /// <summary>
    /// PATH 之外再探这几个前缀。后端进程的 PATH 可能是 launchd 的最小集合，
    /// 这三个位置覆盖了这台机器上 Homebrew / 手动安装 / MacPorts 的落点。
    /// </summary>
    public static readonly IReadOnlyList<string> CommonPrefixes = new[]
    {
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/opt/local/bin",
    };

    /// <summary>
    /// 三个外部工具的声明。<c>Name</c> / <c>EnvVar</c> 与安装期记录文件的键名、
    /// 与后端 <c>backend/tool_paths.py</c> 读取的环境变量名必须逐字一致
    /// （tests/backend_probe/test_tool_paths.py 盯着这条约定）。
    /// </summary>
    public static readonly IReadOnlyList<ToolSpec> Tools = new List<ToolSpec>
    {
        new ToolSpec("aria2c", "JMD_ARIA2C_PATH", "brew install aria2"),
        new ToolSpec("ffprobe", "JMD_FFPROBE_PATH", "brew install ffmpeg"),
        new ToolSpec("ffmpeg", "JMD_FFMPEG_PATH", "brew install ffmpeg"),
    };

    /// <summary>一个外部工具的声明。</summary>
    public sealed class ToolSpec
    {
        /// <summary>Initializes a new instance of the <see cref="ToolSpec"/> class.</summary>
        public ToolSpec(string name, string envVar, string installHint)
        {
            Name = name;
            EnvVar = envVar;
            InstallHint = installHint;
        }

        /// <summary>Gets 命令名，也是记录文件里的键名。</summary>
        public string Name { get; }

        /// <summary>Gets 注入给后端的绝对路径环境变量名。</summary>
        public string EnvVar { get; }

        /// <summary>Gets 缺失时的安装命令（写进配置页/日志的那句话）。</summary>
        public string InstallHint { get; }
    }

    /// <summary>单个工具的解析结果。</summary>
    public sealed class Resolution
    {
        /// <summary>Initializes a new instance of the <see cref="Resolution"/> class.</summary>
        public Resolution(string name, string envVar, string? path, string source, string installHint)
        {
            Name = name;
            EnvVar = envVar;
            Path = path;
            Source = source;
            InstallHint = installHint;
        }

        /// <summary>Gets 命令名。</summary>
        public string Name { get; }

        /// <summary>Gets 注入给后端的绝对路径环境变量名。</summary>
        public string EnvVar { get; }

        /// <summary>Gets 绝对路径；没解析到就是 null。</summary>
        public string? Path { get; }

        /// <summary>Gets 来源说明（配置页指定 / install.sh 记录 / PATH / 常见位置 / 未找到）。</summary>
        public string Source { get; }

        /// <summary>Gets 缺失时的安装命令。</summary>
        public string InstallHint { get; }

        /// <summary>Gets 是否解析到了可执行文件。</summary>
        public bool Ok => !string.IsNullOrEmpty(Path);
    }

    /// <summary>Gets 插件安装目录（DLL 所在目录；记录文件与它同级）。</summary>
    public static string PluginDirectory()
    {
        var directory = Path.GetDirectoryName(typeof(Plugin).Assembly.Location);
        return string.IsNullOrEmpty(directory) ? "." : directory;
    }

    /// <summary>Gets 安装期记录文件的完整路径。</summary>
    public static string RecordPath() => Path.Combine(PluginDirectory(), RecordFileName);

    /// <summary>解析三个工具（配置项按工具名给出，留空表示不限）。</summary>
    public static IReadOnlyList<Resolution> ResolveAll(string? aria2c, string? ffprobe, string? ffmpeg)
    {
        var configured = new Dictionary<string, string?>(StringComparer.Ordinal)
        {
            ["aria2c"] = aria2c,
            ["ffprobe"] = ffprobe,
            ["ffmpeg"] = ffmpeg,
        };

        var recorded = ReadRecord();
        return Tools
            .Select(spec => Resolve(spec, configured.TryGetValue(spec.Name, out var value) ? value : null, recorded))
            .ToList();
    }

    /// <summary>
    /// 解析单个工具：配置项 → 记录文件 → PATH → 常见前缀；解析不到返回 null。
    /// 不抛异常（记录文件损坏、权限不足都按「没有记录」处理）。
    /// </summary>
    public static string? ResolveExecutable(string name, string? configured)
    {
        var spec = Tools.FirstOrDefault(item => string.Equals(item.Name, name, StringComparison.Ordinal));
        if (spec is null)
        {
            return null;
        }

        return Resolve(spec, configured, ReadRecord()).Path;
    }

    /// <summary>把「缺哪些工具、怎么装」拼成一句给配置页和日志看的话；都不缺时返回空串。</summary>
    public static string DescribeMissing(IEnumerable<Resolution> resolutions)
    {
        var missing = resolutions.Where(item => !item.Ok).ToList();
        if (missing.Count == 0)
        {
            return string.Empty;
        }

        var hints = missing
            .Select(item => item.InstallHint)
            .Where(hint => hint.Length > 0)
            .Distinct(StringComparer.Ordinal);

        return "缺少下载工具：" + string.Join("、", missing.Select(item => item.Name))
            + "。装上后点「重启后端」：" + string.Join("；", hints);
    }

    /// <summary>
    /// 把已解析到的工具所在目录拼成一段 PATH 前缀（同目录只留一次），
    /// 由 <see cref="BackendManager"/> 前置进后端子进程的 PATH。
    /// </summary>
    public static string ToolPathPrefix(IEnumerable<Resolution> resolutions)
    {
        var directories = resolutions
            .Where(item => item.Ok)
            .Select(item => Path.GetDirectoryName(item.Path!))
            .Where(directory => !string.IsNullOrEmpty(directory))
            .Distinct(StringComparer.Ordinal)
            .ToList();

        return string.Join(Path.PathSeparator.ToString(), directories!);
    }

    /// <summary>读安装期记录文件；文件不存在、解析失败都返回空表（当作「没有记录」）。</summary>
    public static IReadOnlyDictionary<string, string> ReadRecord()
    {
        var result = new Dictionary<string, string>(StringComparer.Ordinal);
        try
        {
            var path = RecordPath();
            if (!File.Exists(path))
            {
                return result;
            }

            using var document = JsonDocument.Parse(File.ReadAllText(path));
            if (!document.RootElement.TryGetProperty(RecordToolsKey, out var tools)
                || tools.ValueKind != JsonValueKind.Object)
            {
                return result;
            }

            foreach (var property in tools.EnumerateObject())
            {
                if (property.Value.ValueKind != JsonValueKind.String)
                {
                    continue;
                }

                var value = property.Value.GetString();
                if (!string.IsNullOrWhiteSpace(value))
                {
                    result[property.Name] = value!;
                }
            }
        }
        catch (Exception)
        {
            // 记录文件坏了（半写、权限、JSON 语法）不该让插件起不来：
            // 当作「没有记录」，继续走 PATH / 常见前缀。
        }

        return result;
    }

    private static Resolution Resolve(
        ToolSpec spec,
        string? configured,
        IReadOnlyDictionary<string, string> recorded)
    {
        var fromConfigured = ResolveConfigured(configured);
        if (fromConfigured is not null)
        {
            return new Resolution(spec.Name, spec.EnvVar, fromConfigured, "配置页指定", spec.InstallHint);
        }

        if (recorded.TryGetValue(spec.Name, out var recordedPath) && IsExecutable(recordedPath))
        {
            return new Resolution(spec.Name, spec.EnvVar, recordedPath, "install.sh 记录", spec.InstallHint);
        }

        var fromPath = WhichLike(spec.Name);
        if (fromPath is not null)
        {
            return new Resolution(spec.Name, spec.EnvVar, fromPath, "PATH", spec.InstallHint);
        }

        foreach (var prefix in CommonPrefixes)
        {
            var candidate = Path.Combine(prefix, spec.Name);
            if (IsExecutable(candidate))
            {
                return new Resolution(spec.Name, spec.EnvVar, candidate, "常见位置", spec.InstallHint);
            }
        }

        return new Resolution(spec.Name, spec.EnvVar, null, "未找到", spec.InstallHint);
    }

    /// <summary>配置项可以是绝对路径，也可以只是命令名（那就当 PATH 查找用）。</summary>
    private static string? ResolveConfigured(string? configured)
    {
        var value = (configured ?? string.Empty).Trim();
        if (value.Length == 0)
        {
            return null;
        }

        if (Path.IsPathRooted(value))
        {
            return IsExecutable(value) ? value : null;
        }

        var fromPath = WhichLike(value);
        if (fromPath is not null)
        {
            return fromPath;
        }

        foreach (var prefix in CommonPrefixes)
        {
            var candidate = Path.Combine(prefix, value);
            if (IsExecutable(candidate))
            {
                return candidate;
            }
        }

        return null;
    }

    /// <summary>PATH 查找（与 shutil.which 同语义：逐段拼、看可执行位）。</summary>
    private static string? WhichLike(string name)
    {
        var path = Environment.GetEnvironmentVariable("PATH") ?? string.Empty;
        foreach (var directory in path.Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries))
        {
            var candidate = Path.Combine(directory.Trim(), name);
            if (IsExecutable(candidate))
            {
                return candidate;
            }
        }

        return null;
    }

    private static bool IsExecutable(string? path)
    {
        try
        {
            if (string.IsNullOrWhiteSpace(path) || !File.Exists(path))
            {
                return false;
            }

            if (OperatingSystem.IsWindows())
            {
                return true;
            }

            var mode = File.GetUnixFileMode(path);
            return (mode & (UnixFileMode.UserExecute | UnixFileMode.GroupExecute | UnixFileMode.OtherExecute))
                != 0;
        }
        catch (Exception)
        {
            // 不存在 / 权限不足 / 竞态：都算「不可用」
            return false;
        }
    }
}
