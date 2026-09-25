using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using Jellyfin.Data.Enums;
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
    private const string ScriptResource = "Jellyfin.Plugin.JellyfinDownloader.Web.inject.js";
    private const string StyleResource = "Jellyfin.Plugin.JellyfinDownloader.Web.style.css";
    private static readonly HttpClient Client = new() { Timeout = TimeSpan.FromMinutes(30) };
    private static readonly object PackageGate = new();
    private static byte[]? _packageZip;
    private static string? _packageChecksum;
    private static readonly string[] AllowedPrefixes =
    {
        "search", "snapshot", "submit", "watch", "verify", "mark", "cleanup", "pan/",
    };

    /// <summary>Gets the injected client script.</summary>
    [HttpGet("script")]
    [AllowAnonymous]
    public IActionResult GetScript()
    {
        NoCache();
        return Content(ReadResource(ScriptResource), "application/javascript; charset=utf-8");
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
        var config = Plugin.Instance?.Configuration;
        return new JsonResult(new
        {
            minScore = config?.MinScore ?? 60,
            BackendPort = config?.BackendPort ?? 8123,
            StagingRoot = config?.StagingRoot ?? string.Empty,
            MediaRoot = config?.MediaRoot ?? string.Empty,
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

        plugin.SaveConfiguration();
        return new JsonResult(new
        {
            ok = true,
            BackendPort = config.BackendPort,
            StagingRoot = config.StagingRoot,
            MediaRoot = config.MediaRoot,
        });
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

    /// <summary>Detects the system python3 (and its version) for the config page.</summary>
    [HttpGet("python/status")]
    [Authorize(Policy = "RequiresElevation")]
    public IActionResult PythonStatus()
    {
        NoCache();
        PythonLocator.Invalidate();
        return new JsonResult(BackendManager.Status());
    }

    /// <summary>Guides the user through installing a usable python3 (never runs sudo).</summary>
    [HttpPost("python/install")]
    [Authorize(Policy = "RequiresElevation")]
    public IActionResult PythonInstall()
    {
        NoCache();
        var guide = PythonInstaller.Guide();
        // 安装动作可能刚把解释器装好，清缓存让「刷新状态」看到最新结果
        PythonLocator.Invalidate();
        return new JsonResult(new
        {
            ok = guide.Ok,
            detail = guide.Detail,
            command = guide.Command,
            url = guide.Url,
            options = guide.Options,
            status = BackendManager.Status(),
        });
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

        var config = Plugin.Instance?.Configuration;
        var baseUrl = string.IsNullOrWhiteSpace(config?.BackendBaseUrl)
            ? "http://127.0.0.1:8123"
            : config!.BackendBaseUrl.TrimEnd('/');
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
}
