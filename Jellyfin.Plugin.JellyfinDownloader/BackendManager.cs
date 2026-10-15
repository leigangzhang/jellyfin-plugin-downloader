using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;
using System.Text.Json;
using System.Threading;
using Jellyfin.Plugin.JellyfinDownloader.Configuration;

namespace Jellyfin.Plugin.JellyfinDownloader;

/// <summary>Starts/stops the local search backend owned by the plugin.</summary>
public static class BackendManager
{
    private const int DefaultPort = 8123;

    /// <summary>
    /// 后端每个 JSON 响应里带的自身标识，取值与 <c>backend/console_server.py</c> 的
    /// <c>SERVICE_ID</c> 必须逐字相同（两边各有守卫盯着）。
    /// </summary>
    private const string ServiceMarker = "jellyfin-downloader-backend";

    /// <summary>
    /// 后端进程允许从 Jellyfin 继承的环境变量白名单。只放 Python 运行所必需的：
    /// 解释器/脚本查找（PATH）、用户目录（HOME）、临时目录（TMPDIR）、语言环境。
    /// 后端自己要的配置全部走 JMD_*，在 Start 里逐个显式注入。
    /// </summary>
    private static readonly string[] InheritedEnvWhitelist =
    {
        "PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "USER",
    };

    /// <summary>端口自增时最多往后试这么多个（8123→8124→…）。</summary>
    private const int PortScanSpan = 20;
    private static readonly object Gate = new();
    private static Process? _process;
    private static int? _activePort;

    /// <summary>
    /// Gets 当前真正监听的端口。插件没有托管后端时退回配置的端口——端口只在
    /// <see cref="Start"/> 里分配，分配前问它拿到的是「上次用的那个」。
    /// </summary>
    public static int EffectivePort => _activePort ?? ConfiguredPort();

    /// <summary>Gets the configured base URL.</summary>
    public static string BaseUrl
    {
        get
        {
            var url = Plugin.Instance?.Configuration?.BackendBaseUrl;
            if (!string.IsNullOrWhiteSpace(url)
                && url.Contains(":" + EffectivePort, StringComparison.Ordinal))
            {
                return url.TrimEnd('/');
            }

            // 端口可能因为被占用而自增过，BaseUrl 跟着实际监听的端口走，
            // 否则代理会打到别人（或上一次留下的）后端上。
            return "http://127.0.0.1:" + EffectivePort;
        }
    }

    /// <summary>
    /// Probes whether <b>our</b> backend answers. 端口上恰好坐着别人的 HTTP 服务时也会
    /// 返回 4xx/2xx，所以不能只看状态码：后端每个 JSON 响应都带 `service` 字段
    /// （`backend/console_server.py` 的 `SERVICE_ID`），只认带这个字段的响应。
    /// 必须读完整响应再解析——那个字段在 JSON 末尾（实测 7.5KB 响应里在偏移 7228），
    /// 只读前若干字节会认不出自己的后端。
    /// </summary>
    public static bool Reachable()
    {
        try
        {
            using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(3) };
            using var response = client.GetAsync(BaseUrl + "/api/snapshot?slug=jdlping").GetAwaiter().GetResult();
            if (!response.IsSuccessStatusCode)
            {
                return false;
            }

            var body = response.Content.ReadAsStringAsync().GetAwaiter().GetResult();
            using var document = JsonDocument.Parse(body);
            return document.RootElement.TryGetProperty("service", out var service)
                && service.ValueKind == JsonValueKind.String
                && string.Equals(service.GetString(), ServiceMarker, StringComparison.Ordinal);
        }
        catch
        {
            return false;
        }
    }

    /// <summary>Gets the backend entry script shipped inside the plugin folder.</summary>
    public static string BundledScriptPath()
    {
        var directory = Path.GetDirectoryName(typeof(Plugin).Assembly.Location) ?? ".";
        return Path.Combine(directory, "backend", "console_server.py");
    }

    /// <summary>Current backend status.</summary>
    public static object Status()
    {
        var config = Plugin.Instance?.Configuration;
        var detected = PythonLocator.Detect(config?.BackendPython);
        var python = PythonLocator.ResolveExecutable(config?.BackendPython);
        var script = string.IsNullOrWhiteSpace(config?.BackendScript) ? BundledScriptPath() : config!.BackendScript;
        var port = EffectivePort;
        // 用户要求的那个端口：与 port 不同时说明它被占用、实际端口是自增出来的，
        // 配置页那行「服务端口」据此显示原因。
        var portRequested = ConfiguredPort();
        var staging = MediaPaths.ResolveStagingRoot(config?.StagingRoot, MediaPaths.MediaRoot);
        var tools = ResolveTools(config);
        var guide = ToolInstaller.Guide(tools);
        var running = false;
        int? pid = null;
        var managed = false;

        lock (Gate)
        {
            if (_process is { HasExited: false })
            {
                running = true;
                pid = _process.Id;
                managed = true;
            }
        }

        if (!running)
        {
            running = Reachable();
        }

        return new
        {
            running,
            pid,
            managed,
            reachable = running,
            python,
            pythonVersion = detected.Version,
            pythonSource = detected.Source,
            pythonOk = detected.Ok,
            pythonFound = detected.Found,
            script,
            port,
            portRequested,
            pythonExists = detected.Found || File.Exists(python),
            scriptExists = File.Exists(script),
            mediaRoot = MediaPaths.MediaRoot,
            mediaRootSource = MediaPaths.Source,
            mediaLibraryPaths = MediaPaths.Locations,
            stagingRoot = staging,
            stagingConfigured = !string.IsNullOrWhiteSpace(config?.StagingRoot),
            mediaRootConfigured = !string.IsNullOrWhiteSpace(config?.MediaRoot),
            // 下载工具：后端探测种子/媒体全靠这三个。配置页据此显示「缺什么、怎么装」，
            // 而不是等到提交时看一个 rc=3。
            tools = tools.Select(tool => new
            {
                name = tool.Name,
                path = tool.Path,
                source = tool.Source,
                ok = tool.Ok,
                installHint = tool.InstallHint,
            }),
            toolsMissing = guide.Needed ? guide.Detail : string.Empty,
            toolsMissingNames = tools.Where(tool => !tool.Ok).Select(tool => tool.Name).ToArray(),
            toolsMissingCommand = guide.Needed ? guide.Command : string.Empty,
            toolsRecordPath = ToolLocator.RecordPath(),
        };
    }

    /// <summary>Starts the backend if it is not already running.</summary>
    public static (bool Ok, string Detail) Start()
    {
        lock (Gate)
        {
            if (_process is { HasExited: false })
            {
                return (true, "已在运行（插件启动）");
            }

            // 端口自增的前提是「异常退出留下的旧后端」不能占着端口。这里只收 pidfile
            // 里记着的那一个进程——端口上如果坐着别人的服务，就走下面的 ResolvePort
            // 往后取端口，绝不去杀不知道是谁的进程。
            StopRecordedBackend();
            Thread.Sleep(200);

            if (Reachable())
            {
                return (true, "已在运行（外部进程）");
            }

            var config = Plugin.Instance?.Configuration;
            if (config is null)
            {
                return (false, "插件未初始化");
            }

            // 配置项留空就用自动探测到的解释器（探测不到才退回 PATH 上的 python3）
            var python = PythonLocator.ResolveExecutable(config.BackendPython);
            var script = string.IsNullOrWhiteSpace(config.BackendScript)
                ? BundledScriptPath()
                : config.BackendScript;

            if (!File.Exists(python))
            {
                return (false, $"python 不存在：{python}");
            }

            if (!File.Exists(script))
            {
                return (false, $"脚本不存在：{script}");
            }

            // 端口分配放在启动前置检查之后：这些检查失败时端口并没有被拿走。
            var requested = ConfiguredPort();
            var port = ResolvePort(requested);
            if (port != requested)
            {
                _activePort = port;
            }

            var log = LogPath();
            // 端口用环境变量传，不拼进命令行：命令行整体交给 `/bin/sh -c`，任何拼进去的
            // 值（这里现在是数字，以后可能是路径）都要再过一层 shell 解析。Python / 脚本 /
            // 日志三条路径已经各自加了单引号，保持原样。
            var command = $"exec '{python}' '{script}' >> '{log}' 2>&1";
            var staging = MediaPaths.ResolveStagingRoot(config.StagingRoot, MediaPaths.MediaRoot);

            // 下载工具（aria2c / ffprobe / ffmpeg）：解析结果既前置进子进程 PATH，
            // 也经 JMD_*_PATH 显式注入（后端 tool_paths.py 优先读它）。缺了不拦启动
            // ——后端还能读快照——但必须留下**说人话**的记录：以前什么都不说，
            // 于是「机器上没装工具」在提交时变成 rc=3「种子里没有可读视频」。
            var tools = ResolveTools(config);
            var toolGuide = ToolInstaller.Guide(tools);
            if (toolGuide.Needed)
            {
                AppendLog("WARN " + toolGuide.Detail);
            }

            var info = new ProcessStartInfo
            {
                FileName = "/bin/sh",
                Arguments = "-c \"" + command + "\"",
                WorkingDirectory = Path.GetDirectoryName(script) ?? "/",
                UseShellExecute = false,
                CreateNoWindow = true,
            };

            // **先清空继承来的环境**：Jellyfin 进程的环境里有它自己的凭据（API key、
            // 会话 token 之类），子进程默认整份继承——后端只需要下面这几个 JMD_* 变量，
            // 没有理由把一个明文凭据环境交给它（本机实测泄漏过 DEEPSEEK_API_KEY /
            // DSH_SESSION_ID 等，见 docs 的复核记录）。清空后只补白名单。
            info.Environment.Clear();

            // 白名单：Python 自己运行所需的最小集合（解释器路径、用户目录、临时目录、
            // 语言环境）。后端代码只读 JMD_* 变量，不需要别的。
            foreach (var name in InheritedEnvWhitelist)
            {
                var value = Environment.GetEnvironmentVariable(name);
                if (!string.IsNullOrEmpty(value))
                {
                    info.Environment[name] = value;
                }
            }

            // 端口：后端 console_server.py 的 --port 默认值取自这个变量。
            info.Environment["JMD_PORT"] = port.ToString(System.Globalization.CultureInfo.InvariantCulture);

            // 工具目录前置进 PATH：launchd 启动的 Jellyfin 只有最小 PATH，不前置的话
            // 后端里任何按裸名调用这三个工具的地方都会 FileNotFoundError。
            var toolPrefix = ToolLocator.ToolPathPrefix(tools);
            if (toolPrefix.Length > 0)
            {
                var inherited = info.Environment.TryGetValue("PATH", out var current) ? current : string.Empty;
                info.Environment["PATH"] = inherited is { Length: > 0 }
                    ? toolPrefix + Path.PathSeparator + inherited
                    : toolPrefix;
            }

            // 显式路径：后端 backend/tool_paths.py 的第一优先级来源。
            foreach (var tool in tools.Where(item => item.Ok))
            {
                info.Environment[tool.EnvVar] = tool.Path!;
            }

            // 路径来源：媒体库根 = Jellyfin 库配置解析结果；暂存区 = 配置页（或默认）。
            // 后端 media_download_lib 读这两个变量，读不到才用它自己的硬编码兜底。
            info.Environment["JMD_MEDIA_ROOT"] = MediaPaths.MediaRoot;
            info.Environment["JMD_STAGING_ROOT"] = staging;

            // 资源站清单与 L1 关键字组合也走环境变量。它们的事实源是
            // backend/source_config.py（默认值、kind 取值、校验都在那边），这里只负责
            // 序列化；空列表不注入，后端读不到就用内置默认值。
            var sitesJson = SerializeBtSites(config.BtSites);
            if (sitesJson.Length > 0)
            {
                info.Environment["JMD_BT_SITES"] = sitesJson;
            }

            var keywordsJson = SerializeKeywords(config.L1Keywords);
            if (keywordsJson.Length > 0)
            {
                info.Environment["JMD_L1_KEYWORDS"] = keywordsJson;
            }

            // TMDb key 只在**配了的时候**注入：空 = 完全不注入这个变量（后端读不到就按
            // 「未配置」处理，热门页只报未配置，不会回退到任何内置 key）。key 不进命令行、
            // 不进日志，只走环境变量。
            var tmdbApiKey = (config.TmdbApiKey ?? string.Empty).Trim();
            if (tmdbApiKey.Length > 0)
            {
                info.Environment["JMD_TMDB_API_KEY"] = tmdbApiKey;
            }

            try
            {
                _process = Process.Start(info);
                _activePort = port;
                return (true, port == requested
                    ? $"已启动（pid {_process?.Id}）"
                    : $"已启动（pid {_process?.Id}，端口 {requested} 已被占用，改用 {port}）");
            }
            catch (Exception ex)
            {
                _activePort = null;
                return (false, ex.Message);
            }
        }
    }

    /// <summary>Stops the backend started by the plugin.</summary>
    public static (bool Ok, string Detail) Stop()
    {
        lock (Gate)
        {
            if (_process is { HasExited: false })
            {
                // 先发 SIGTERM：后端收到后会按进程组收掉它的子进程（含 aria2c），
                // 再删 pidfile 退出。直接 SIGKILL 会跳过这步、留下孤儿。
                var pid = _process.Id;
                TrySignal(pid, "TERM");
                if (!_process.WaitForExit(5000))
                {
                    try
                    {
                        _process.Kill(true);
                        _process.WaitForExit(3000);
                    }
                    catch
                    {
                        // ignore
                    }
                }
                _process = null;
                return (true, "已停止");
            }
        }

        return Reachable()
            ? (false, "后端由外部进程启动，插件无法停止")
            : (true, "本就未运行");
    }

    /// <summary>Stops the backend the plugin owns and starts a fresh one.</summary>
    public static (bool Ok, string Detail) Restart()
    {
        // 只回收插件自己启动过的进程：正常路径由 Stop() 按 pid 收，异常退出留下的
        // 那些靠 pidfile 认领。**不按端口杀**——端口上坐着的可能是别人的服务。
        Stop();
        StopRecordedBackend();
        _activePort = null;
        Thread.Sleep(600);
        var result = Start();
        return (result.Ok, "已重启：" + result.Detail);
    }

    /// <summary>
    /// 收掉后端自己记在 pidfile 里的进程（插件重启/崩溃后留下的孤儿）。
    /// pidfile 由后端在启动时写入、退出时删除，所以里面的 pid 只可能是这个后端；
    /// 读不到就直接返回，绝不去猜「谁占着端口」。
    ///
    /// 已知的窄边界：后端被 SIGKILL 后 pid 恰好被系统复用给别的进程时，这里可能向
    /// 那个无关进程发 TERM（pidfile 无随机 token，凭 pid 无法百分之百确认身份）。
    /// 不为此加机制：插件的启停基本发生在有用户交互时，pid 复用的前提是插件崩溃
    /// 后有新进程恰好拿到同一个 pid——概率低到不值得再加一层状态。
    /// </summary>
    private static void StopRecordedBackend()
    {
        var pid = ReadRecordedBackendPid();
        if (pid is null)
        {
            return;
        }

        TrySignal(pid.Value, "TERM");
        for (var waited = 0; waited < 1500; waited += 100)
        {
            Thread.Sleep(100);
            if (!ProcessAlive(pid.Value))
            {
                return;
            }
        }

        TrySignal(pid.Value, "KILL");
    }

    /// <summary>读后端 pidfile；文件不存在、内容不是 pid、进程已死都返回 null。</summary>
    private static int? ReadRecordedBackendPid()
    {
        try
        {
            var script = string.IsNullOrWhiteSpace(Plugin.Instance?.Configuration?.BackendScript)
                ? BundledScriptPath()
                : Plugin.Instance!.Configuration.BackendScript;
            var directory = Path.GetDirectoryName(script);
            if (string.IsNullOrEmpty(directory))
            {
                return null;
            }

            // 后端数据根默认是脚本同级的 state/（JMD_DATA_DIR 可覆盖，这里只认默认位置：
            // 环境变量是后端启动参数，插件侧读不到同一个值时宁可不动任何进程）。
            var pidFile = Path.Combine(directory, "state", "console_server.pid");
            if (!File.Exists(pidFile))
            {
                return null;
            }

            var text = File.ReadAllText(pidFile).Trim();
            if (!int.TryParse(text, out var pid) || pid <= 0)
            {
                return null;
            }

            return ProcessAlive(pid) ? pid : null;
        }
        catch (IOException)
        {
            return null;
        }
        catch (UnauthorizedAccessException)
        {
            return null;
        }
    }

    private static bool ProcessAlive(int pid)
    {
        try
        {
            using var process = Process.GetProcessById(pid);
            return !process.HasExited;
        }
        catch (ArgumentException)
        {
            return false;
        }
        catch (InvalidOperationException)
        {
            return false;
        }
    }

    /// <summary>
    /// 解析三个下载工具：配置项 → install.sh 记录文件 → PATH → 常见前缀
    /// （顺序的定义在 <see cref="ToolLocator.ResolveAll"/>）。
    /// </summary>
    private static IReadOnlyList<ToolLocator.Resolution> ResolveTools(PluginConfiguration? config)
        => ToolLocator.ResolveAll(config?.BackendAria2c, config?.BackendFfprobe, config?.BackendFfmpeg);

    /// <summary>
    /// 往后端日志文件追加一行（与后端 stdout 同一个文件）。日志写不进去不影响启动：
    /// 配置页的 backend/status 是同一份信息，丢一条日志不至于让人看不到缺失项。
    /// </summary>
    private static void AppendLog(string line)
    {
        try
        {
            File.AppendAllText(
                LogPath(),
                $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {line}{Environment.NewLine}");
        }
        catch (Exception)
        {
            // ignore
        }
    }

    /// <summary>配置里写的端口；没配或配得不合法时用默认端口。</summary>
    private static int ConfiguredPort()
    {
        var port = Plugin.Instance?.Configuration?.BackendPort ?? 0;
        return port is > 0 and <= 65535 ? port : DefaultPort;
    }

    /// <summary>
    /// 端口从 <paramref name="requested"/> 起递增，取第一个没人监听的。
    /// 上限是 <paramref name="requested"/> 之后 <see cref="PortScanSpan"/> 个端口：
    /// 连续这么多端口都被占是异常情况，那时宁可复用 requested 让启动失败可见。
    /// </summary>
    private static int ResolvePort(int requested)
    {
        for (var port = requested; port < requested + PortScanSpan; port++)
        {
            if (port > 65535)
            {
                break;
            }

            if (IsPortFree(port))
            {
                return port;
            }
        }

        return requested;
    }

    /// <summary>
    /// 端口能不能拿来监听。bind 失败也别急着判「占用」：TIME_WAIT 残留下来的
    /// 连接会让 bind 失败但并没有进程在听，所以再用 connect 复核一次。
    /// </summary>
    private static bool IsPortFree(int port)
    {
        TcpListener? probe = null;
        try
        {
            probe = new TcpListener(IPAddress.Loopback, port);
            probe.Start();
            return true;
        }
        catch (SocketException)
        {
            return !IsPortListening(port);
        }
        finally
        {
            try
            {
                probe?.Stop();
            }
            catch (SocketException)
            {
                // 探测失败不影响结论：停止监听本身没有别的失败模式。
            }
        }
    }

    private static bool IsPortListening(int port)
    {
        try
        {
            using var client = new TcpClient();
            return client.ConnectAsync(IPAddress.Loopback, port).Wait(TimeSpan.FromMilliseconds(200))
                && client.Connected;
        }
        catch (Exception)
        {
            return false;
        }
    }

    /// <summary>
    /// Serializes the configured stations for <c>JMD_BT_SITES</c>. An empty string
    /// means "do not inject": the backend then uses its built-in defaults.
    /// </summary>
    private static string SerializeBtSites(IEnumerable<BtSiteConfig>? sites)
    {
        var payload = new List<object>();
        foreach (var site in sites ?? Enumerable.Empty<BtSiteConfig>())
        {
            var baseUrl = (site.BaseUrl ?? string.Empty).Trim();
            var webSearchUrl = (site.WebSearchUrl ?? string.Empty).Trim();
            // 只填「网页搜索地址」的站是合法的云盘专用站，不能在这里被丢掉。
            if (baseUrl.Length == 0 && webSearchUrl.Length == 0)
            {
                continue;
            }

            payload.Add(new
            {
                name = (site.Name ?? string.Empty).Trim(),
                // 没有接口地址时类型无意义，后端会把它归一化成空串（云盘专用站）。
                kind = baseUrl.Length == 0
                    ? string.Empty
                    : (site.Kind ?? string.Empty).Trim().ToLowerInvariant(),
                base_url = baseUrl,
                web_search_url = webSearchUrl,
            });
        }

        return payload.Count == 0 ? string.Empty : JsonSerializer.Serialize(payload);
    }

    /// <summary>
    /// Serializes the L1 keyword combinations for <c>JMD_L1_KEYWORDS</c>; an empty
    /// string means "do not inject" and the backend uses its built-in defaults.
    /// </summary>
    private static string SerializeKeywords(IEnumerable<string>? keywords)
    {
        var payload = (keywords ?? Enumerable.Empty<string>())
            .Select(keyword => (keyword ?? string.Empty).Trim())
            .Where(keyword => keyword.Length > 0)
            .Distinct()
            .ToList();
        return payload.Count == 0 ? string.Empty : JsonSerializer.Serialize(payload);
    }

    /// <summary>Stops the backend on host shutdown (only if the plugin started it).</summary>
    public static void StopQuietly()
    {
        lock (Gate)
        {
            if (_process is { HasExited: false })
            {
                TrySignal(_process.Id, "TERM");
                if (!_process.WaitForExit(3000))
                {
                    try
                    {
                        _process.Kill(true);
                    }
                    catch
                    {
                        // ignore
                    }
                }
                _process = null;
            }
        }
    }

    /// <summary>Sends a signal to a pid using /bin/kill (SIGTERM/SIGKILL).</summary>
    private static void TrySignal(int pid, string signal)
    {
        try
        {
            using var kill = Process.Start(new ProcessStartInfo
            {
                FileName = "/bin/kill",
                Arguments = $"-{signal} {pid}",
                UseShellExecute = false,
                CreateNoWindow = true,
            });
            kill?.WaitForExit(3000);
        }
        catch
        {
            // ignore
        }
    }

    private static string LogPath()
    {
        var baseDir = Path.GetTempPath();
        var configPath = Plugin.Instance?.ConfigurationFilePath;
        if (!string.IsNullOrEmpty(configPath))
        {
            var dir = Path.GetDirectoryName(configPath);
            if (!string.IsNullOrEmpty(dir) && Directory.Exists(dir))
            {
                baseDir = dir;
            }
        }

        return Path.Combine(baseDir, "jellyfin-downloader-backend.log");
    }
}
