using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Hosting;

namespace Jellyfin.Plugin.JellyfinDownloader;

/// <summary>
/// 随 Jellyfin 主进程启动/退出而拉起/关闭后端。
/// 插件加载（启用）即启动后端，不需要配置页里的单独「启动」按钮；
/// 后端已在运行时 Start() 会直接识别、不会重复拉起。
/// </summary>
public class BackendHostedService : IHostedService
{
    public Task StartAsync(CancellationToken cancellationToken)
    {
        try
        {
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
