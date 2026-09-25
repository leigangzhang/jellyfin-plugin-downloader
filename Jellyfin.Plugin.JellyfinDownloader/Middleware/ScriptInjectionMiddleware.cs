using System;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Http;

namespace Jellyfin.Plugin.JellyfinDownloader;

/// <summary>Appends the plugin script tag to the web client entry document.</summary>
public class ScriptInjectionMiddleware
{
    private const string ScriptTag = "<script defer src=\"/JellyfinDownloader/script?v=20260925i\"></script>";
    private const string StyleTag = "<link rel=\"stylesheet\" href=\"/JellyfinDownloader/style?v=20260925i\">";
    private readonly RequestDelegate _next;

    public ScriptInjectionMiddleware(RequestDelegate next)
    {
        _next = next;
    }

    public async Task InvokeAsync(HttpContext context)
    {
        if (!ShouldInspect(context))
        {
            await _next(context).ConfigureAwait(false);
            return;
        }

        var originalBody = context.Response.Body;
        using var buffer = new MemoryStream();
        context.Response.Body = buffer;
        try
        {
            await _next(context).ConfigureAwait(false);
        }
        finally
        {
            context.Response.Body = originalBody;
        }

        buffer.Position = 0;
        var contentType = context.Response.ContentType ?? string.Empty;
        if (buffer.Length == 0 || contentType.IndexOf("text/html", StringComparison.OrdinalIgnoreCase) < 0)
        {
            await buffer.CopyToAsync(originalBody).ConfigureAwait(false);
            return;
        }

        string html;
        using (var reader = new StreamReader(buffer, Encoding.UTF8))
        {
            html = await reader.ReadToEndAsync().ConfigureAwait(false);
        }

        if (html.IndexOf("JellyfinDownloader/script", StringComparison.Ordinal) < 0)
        {
            var payload = StyleTag + ScriptTag;
            var index = html.LastIndexOf("</body>", StringComparison.OrdinalIgnoreCase);
            html = index >= 0 ? html.Insert(index, payload) : html + payload;
        }

        var bytes = Encoding.UTF8.GetBytes(html);
        context.Response.Headers.Remove("Content-Encoding");
        context.Response.ContentLength = bytes.Length;
        await originalBody.WriteAsync(bytes).ConfigureAwait(false);
    }

    private static bool ShouldInspect(HttpContext context)
    {
        if (!HttpMethods.IsGet(context.Request.Method))
        {
            return false;
        }

        var path = context.Request.Path.Value ?? "/";
        return path.Equals("/", StringComparison.OrdinalIgnoreCase)
            || path.Equals("/web", StringComparison.OrdinalIgnoreCase)
            || path.Equals("/web/", StringComparison.OrdinalIgnoreCase)
            || path.Equals("/web/index.html", StringComparison.OrdinalIgnoreCase);
    }
}
