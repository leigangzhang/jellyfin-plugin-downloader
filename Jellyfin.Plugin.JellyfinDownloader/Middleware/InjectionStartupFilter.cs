using System;
using MediaBrowser.Controller.Library;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;

namespace Jellyfin.Plugin.JellyfinDownloader;

/// <summary>Inserts the injection middleware at the very start of the pipeline.</summary>
public class InjectionStartupFilter : IStartupFilter
{
    /// <inheritdoc />
    public Action<IApplicationBuilder> Configure(Action<IApplicationBuilder> next)
    {
        return app =>
        {
            var lifetime = app.ApplicationServices.GetService<IHostApplicationLifetime>();
            lifetime?.ApplicationStopping.Register(BackendManager.StopQuietly);
            // 启动时就先按 Jellyfin 的媒体库配置解析一次落点根目录，
            // 之后每次配置页读写/启动后端再刷新。
            MediaPaths.Refresh(
                app.ApplicationServices.GetService<ILibraryManager>(),
                Plugin.Instance?.Configuration?.MediaRoot);
            app.UseMiddleware<ScriptInjectionMiddleware>();
            next(app);
        };
    }
}
