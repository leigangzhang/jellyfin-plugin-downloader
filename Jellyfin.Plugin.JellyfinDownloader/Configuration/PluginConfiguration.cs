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

    /// <summary>
    /// Gets the BT resource stations the backend searches. Empty means the
    /// backend's built-in defaults (the two stations this plugin shipped with);
    /// the defaults, the accepted <c>Kind</c> values and the validation live in
    /// <c>backend/source_config.py</c>.
    /// </summary>
    public List<BtSiteConfig> BtSites { get; set; } = new();

    /// <summary>
    /// Gets the L1 keyword combinations. Each entry is appended to the film title
    /// and asked before every other query tier, in order. Empty means the
    /// backend's built-in defaults.
    /// </summary>
    public List<string> L1Keywords { get; set; } = new();
}
