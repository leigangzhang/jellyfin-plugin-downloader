using MediaBrowser.Model.Plugins;

namespace Jellyfin.Plugin.JellyfinDownloader.Configuration;

/// <summary>Plugin settings.</summary>
public class PluginConfiguration : BasePluginConfiguration
{
    /// <summary>Gets or sets the minimum score shown in the panel.</summary>
    public double MinScore { get; set; } = 60;

    /// <summary>Gets or sets the local search/scoring backend base URL.</summary>
    public string BackendBaseUrl { get; set; } = "http://127.0.0.1:8123";

    /// <summary>
    /// Gets or sets the Python interpreter used to run the backend.
    /// Empty means <c>python3</c> from PATH.
    /// </summary>
    public string BackendPython { get; set; } = string.Empty;

    /// <summary>Gets or sets the backend entry script; empty means the bundled backend/console_server.py.</summary>
    public string BackendScript { get; set; } = string.Empty;

    /// <summary>Gets or sets the backend port.</summary>
    public int BackendPort { get; set; } = 8123;

    /// <summary>
    /// Gets or sets the staging directory the downloader writes into before
    /// finalizing. Empty means the same-volume default: the sibling
    /// <c>.staging</c> directory of the media root.
    /// </summary>
    public string StagingRoot { get; set; } = string.Empty;

    /// <summary>
    /// Gets or sets the media library root (the directory that contains
    /// <c>Movies</c> / <c>TV Shows</c> / <c>Shows</c> / <c>Records</c>).
    /// Empty means "resolve it from the Jellyfin library configuration".
    /// </summary>
    public string MediaRoot { get; set; } = string.Empty;
}
