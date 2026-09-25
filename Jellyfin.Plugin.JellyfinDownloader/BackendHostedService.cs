using System.Threading;
using System.Threading.Tasks;
using MediaBrowser.Controller.Library;
using Microsoft.Extensions.Hosting;

namespace Jellyfin.Plugin.JellyfinDownloader;

/// <summary>
/// 随 Jellyfin 主进程启动/退出而拉起/关闭后端。
/// 插件加载（启用）即启动后端，不需要配置页里的单独「启动」按钮；
/// 后端已在运行时 Start() 会直接识别、不会重复拉起。
/// </summary>
public class BackendHostedService : IHostedService
{
    private readonly ILibraryManager _library;

    public BackendHostedService(ILibraryManager library)
    {
        _library = library;
    }

    public Task StartAsync(CancellationToken cancellationToken)
    {
        try
        {
            // 主进程已启动、媒体库已加载后，再按当前配置解析一次媒体根，
            // 避免后端在启动早期拿到占位默认（~/Media）而不是真实库根。
            MediaPaths.Refresh(_library, Plugin.Instance?.Configuration?.MediaRoot);
            BackendManager.Start();
        }
        catch
        {
            // 启动失败不阻断 Jellyfin；详情面板/配置页仍会兜底重试。
        }

        return Task.CompletedTask;
    }

    public Task StopAsync(CancellationToken cancellationToken)
    {
        // 主进程退出时优雅停掉自己拉起的后端（SIGTERM → 清理子进程 → 删 pidfile）。
        BackendManager.StopQuietly();
        return Task.CompletedTask;
    }
}
