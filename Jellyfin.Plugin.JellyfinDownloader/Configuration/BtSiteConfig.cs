namespace Jellyfin.Plugin.JellyfinDownloader.Configuration;

/// <summary>
/// One BT resource station the backend searches.
/// <see cref="Kind"/> selects the adapter that parses this station's responses,
/// <see cref="BaseUrl"/> is its API endpoint, and <see cref="WebSearchUrl"/> is the
/// optional page the cloud-drive channel fetches (it must contain a <c>{q}</c>
/// placeholder). The defaults, the accepted kinds and the validation live in
/// <c>backend/source_config.py</c> -- this class is only the configuration surface.
/// </summary>
public class BtSiteConfig
{
    /// <summary>Gets or sets the display name used in logs.</summary>
    public string Name { get; set; } = string.Empty;

    /// <summary>Gets or sets the adapter kind: <c>diao</c> or <c>torrents-csv</c>.</summary>
    public string Kind { get; set; } = "diao";

    /// <summary>Gets or sets the station's API endpoint.</summary>
    public string BaseUrl { get; set; } = string.Empty;

    /// <summary>Gets or sets the optional web-search URL used by the cloud-drive channel.</summary>
    public string WebSearchUrl { get; set; } = string.Empty;
}
