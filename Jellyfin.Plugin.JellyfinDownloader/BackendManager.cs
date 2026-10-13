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

    /// <summary>Probes whether the backend answers.</summary>
    public static bool Reachable()
    {
        try
        {
            using var client = new HttpClient { Timeout = TimeSpan.FromMilliseconds(1500) };
            using var response = client.GetAsync(BaseUrl + "/api/snapshot?slug=jdlping").GetAwaiter().GetResult();
            return (int)response.StatusCode < 500;
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

            // 端口自增的前提是「异常退出留下的旧后端」不能占着端口：先按记录的端口
            // 收掉它。只认插件自己启动过的那个端口，不会去动别人的进程。
            KillByPort(EffectivePort);
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
            var command = $"exec '{python}' '{script}' --port {port} >> '{log}' 2>&1";
            var staging = MediaPaths.ResolveStagingRoot(config.StagingRoot, MediaPaths.MediaRoot);
            var info = new ProcessStartInfo
            {
                FileName = "/bin/sh",
                Arguments = "-c \"" + command + "\"",
                WorkingDirectory = Path.GetDirectoryName(script) ?? "/",
                UseShellExecute = false,
                CreateNoWindow = true,
            };

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

    /// <summary>Stops whatever is listening on the backend port and starts a fresh backend.</summary>
    public static (bool Ok, string Detail) Restart()
    {
        // 先放掉插件自己那个端口：端口已经被外部进程占用时不去杀它，
        // Start 会按 8123 起往上找下一个可用端口。
        var port = EffectivePort;
        Stop();
        _activePort = null;
        KillByPort(port);
        Thread.Sleep(600);
        var result = Start();
        return (result.Ok, "已重启：" + result.Detail);
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

    private static void KillByPort(int port)
    {
        try
        {
            var info = new ProcessStartInfo
            {
                FileName = "/usr/sbin/lsof",
                Arguments = $"-ti tcp:{port}",
                RedirectStandardOutput = true,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            using var lsof = Process.Start(info);
            if (lsof is null)
            {
                return;
            }

            var output = lsof.StandardOutput.ReadToEnd();
            lsof.WaitForExit(3000);
            foreach (var line in output.Split('\n'))
            {
                if (int.TryParse(line.Trim(), out var pid))
                {
                    try
                    {
                        Process.GetProcessById(pid).Kill(true);
                    }
                    catch
                    {
                        // ignore
                    }
                }
            }
        }
        catch
        {
            // ignore
        }
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
