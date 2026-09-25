using System;
using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Threading;

namespace Jellyfin.Plugin.JellyfinDownloader;

/// <summary>Starts/stops the local search backend owned by the plugin.</summary>
public static class BackendManager
{
    /// <summary>未配置解释器时用 PATH 上的 python3（源码里不放机器相关路径）。</summary>
    private const string DefaultPython = "python3";
    private const int DefaultPort = 8123;
    private static readonly object Gate = new();
    private static Process? _process;

    /// <summary>Gets the configured base URL.</summary>
    public static string BaseUrl
    {
        get
        {
            var url = Plugin.Instance?.Configuration?.BackendBaseUrl;
            return string.IsNullOrWhiteSpace(url) ? "http://127.0.0.1:8123" : url.TrimEnd('/');
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
        var python = string.IsNullOrWhiteSpace(config?.BackendPython) ? DefaultPython : config!.BackendPython;
        var script = string.IsNullOrWhiteSpace(config?.BackendScript) ? BundledScriptPath() : config!.BackendScript;
        var port = config?.BackendPort is > 0 ? config!.BackendPort : DefaultPort;
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
            script,
            port,
            pythonExists = File.Exists(python),
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

            if (Reachable())
            {
                return (true, "已在运行（外部进程）");
            }

            var config = Plugin.Instance?.Configuration;
            if (config is null)
            {
                return (false, "插件未初始化");
            }

            var python = string.IsNullOrWhiteSpace(config.BackendPython) ? DefaultPython : config.BackendPython;
            var script = string.IsNullOrWhiteSpace(config.BackendScript)
                ? BundledScriptPath()
                : config.BackendScript;
            var port = config.BackendPort <= 0 ? DefaultPort : config.BackendPort;

            if (!File.Exists(python))
            {
                return (false, $"python 不存在：{python}");
            }

            if (!File.Exists(script))
            {
                return (false, $"脚本不存在：{script}");
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

            try
            {
                _process = Process.Start(info);
                return (true, $"已启动（pid {_process?.Id}）");
            }
            catch (Exception ex)
            {
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

    /// <summary>Stops whatever is listening on the port and starts a fresh backend.</summary>
    public static (bool Ok, string Detail) Restart()
    {
        var config = Plugin.Instance?.Configuration;
        var port = config?.BackendPort is > 0 ? config!.BackendPort : DefaultPort;
        Stop();
        KillByPort(port);
        Thread.Sleep(600);
        var result = Start();
        return (result.Ok, "已重启：" + result.Detail);
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
