# Proposal: A trending entry in the downloader plugin, with a followed title anchored into the Jellyfin library

English | [中文](2026-10-15-console-trending-anchor.zh.md)

Status: proposed
Date: 2026-10-15
Class: feature

## Problem

The plugin can only be entered from an item the library already knows: the detail-page button, or the header button's manual form. There is no way to act on a title that is not in the library yet, and nothing records "I want this" between seeing a title and downloading it.

Three gaps, measured on this machine rather than assumed:

1. **No discovery surface.** Nothing in the plugin reaches TMDb, and the library cannot answer "what is worth watching right now" either: of its 170 series, 159 hold zero episodes.
2. **No wish state.** A title the user wants is remembered nowhere; the manual form is retyped every time.
3. **Followed titles have no home in the library.** Library pages for undownloaded titles are built by hand today, and the shape is uniform.

That third gap is also the reason this proposal is small: the mechanism already exists, only unautomated. All 75 movies in `Movies/` are the same 8,307-byte placeholder MP4 (md5 `59563f02cff1daea13f1082b80fa695c`), each folder carrying `movie.nfo`, `poster.jpg` and `fanart.jpg`. Cast arrives as well: `PeopleBaseItemMap` holds 4,753 rows and the sample movie resolves five actors with roles, even though the NFO carries no `<actor>` element.

## Proposal

Add a second header entry, **Trending**, that lists TMDb trending and popular movies and series and can anchor a followed title into the library.

**Data.** A new `backend/trending/` package: `trending_source.py` (TMDb client over `urllib.request`, same shape as `backend/search/search_pool_api.py`), `trending_cache.py` (TTL cache, atomic write via a temp file plus `os.replace`, stale fallback), `trending_watchlist.py`. Two routes join `backend/console_server/http_api.py`: `GET /api/trending` and `POST /api/trending/watch`. The data root comes from `media_download_lib`'s `DATA_ROOT`; the package must not build paths from `__file__`.

**Surfaces.** `Web/inject_06_trending.js` renders the posters as an overlay and reuses the existing `openManualPanel({title, year, kind})`, so picking a trending title lands in the scoring flow that already ships. Its header button must use its own class `.jdl-trending-btn` and its own icon `trending_up`, because `enforceSingleEntry()` and `removeHeaderButton()` in `inject_01_core.js` collapse every header node carrying `.material-icons.arrow_downward` and would otherwise delete one of the two entries. The three places that register a script fragment must move together: the file in `Web/`, the `EmbeddedResource` entry in the csproj, and `ScriptSegments`.

**Library state.** `POST /JellyfinDownloader/trending/library-status` answers "already in the library" for a batch of titles through `ILibraryManager`, in the shape of the existing `seasons` endpoint. `POST /JellyfinDownloader/trending/anchor` writes the placeholder. Both live in the plugin rather than the backend: `MediaPaths` and `ILibraryManager.GetVirtualFolders()` are the only source of library paths, and `backend/` is forbidden from carrying machine-specific paths.

**Placeholder naming.** The template ships as `Web/placeholder.mp4` (8,307 bytes, the file the library already uses) and is written as:

```
Movies/<title> (<year>)/<title> (<year>) - zz placeholder.mp4
TV Shows/<title> (<year>)/Season 01/<title> S01E01 - zz placeholder.mp4
```

Two constraints force this shape, both verified rather than assumed. First, `atomic_same_volume_move` raises `FileExistsError` when the destination exists, and `normalized_destination` puts the real file at exactly `<title> (<year>).<ext>`; an unlabelled placeholder would therefore break archiving whenever the download is an MP4. Second, Jellyfin treats files in one folder as versions only when the filename begins character-for-character with the folder name before a version label, and it plays the first version of an alphabetically sorted list, so the label must both separate it from the real name and sort last.

**Settings.** `TmdbApiKey`, `TmdbLanguage` (default `zh-CN`), `TrendingLists` and `AnchorEnabled` (default false) join `PluginConfiguration`; the configuration page gains one group. The key travels to the backend as `JMD_TMDB_API_KEY`. `AnchorEnabled` off means zero writes to the library.

**Not in this change.** No automatic download, no batch submit, no library row, no route hijack, no Seerr or *arr integration, and no deletion of placeholders. Cleanup, if it is ever wanted, belongs to the watcher stage.

## Alternatives considered

**Route the discovery through Seerr and an existing plugin.** `SeerrFin` (TMDb trending carousels, request modals) and `Home Screen Sections` (Jellyseerr Trending and Discover rows) both solve discovery, but both target ABI 12.0 while this server runs 10.11.11, and both require a Seerr instance that is not deployed here. They also end in Sonarr/Radarr, which this setup does not use.

**Read the trending lists off the configured resource stations.** Zero new credentials and the results are directly downloadable, but a station's "hot" page is only its own latest releases, it carries no poster, cast or rating, and every station redesign breaks it. It stays a possible second provider.

**Douban as the source.** No official API; scraping is unstable and gets the caller blocked. Rejected.

**Let the browser call TMDb directly.** Exposes the key to every client and breaks the existing same-origin proxy boundary. Rejected.

**A real route at `/web/#/jdl/trending`.** Needs either route hijacking or the Plugin Pages dependency; the overlay is smaller and keeps the plugin self-contained.

**Let the real file overwrite the placeholder at archive time.** `atomic_same_volume_move` refuses to overwrite an existing destination, and the user explicitly asked that archiving leave the placeholder alone, so the collision is avoided by naming instead.

**Delete the placeholder once the real file lands.** It would remove the second version entirely, but the user ruled it out; if the first version ever resolves to the placeholder during playback, the fallback is a cleanup owned by the watcher stage.

**Record follows in the watchlist only, without touching the library.** Cheapest, and it loses the whole point: a followed title would have no library page, no poster and no cast until the download lands.

## Acceptance criteria

- [A1] With no key configured, the trending page renders guidance and the console stays clean — owned by `source`.
- [A2] `GET /api/trending` returns at least two lists of at least ten items against a fake opener — owned by `tests`.
- [A3] A second call inside the TTL never reaches TMDb, proven with an injected clock — owned by `tests`.
- [A4] An unreachable TMDb returns the previous cache with the stale flag set — owned by `tests`.
- [A5] Following a movie leaves `Movies/<title> (<year>)/<title> (<year>) - zz placeholder.mp4` on disk — owned by `source`.
- [A6] Following a series leaves `Season 01/<title> S01E01 - zz placeholder.mp4` and the library shows the series with one episode — owned by `source`.
- [A7] The placeholder path never equals the `normalized_destination` path for the same title and year — owned by `tests`.
- [A8] A directory holding both the placeholder and a real file still reports the already-exists verdict from `check_exists` — owned by `tests`.
- [A9] Repeating a follow reports exists without rewriting the file, and a disabled anchor switch writes nothing — owned by `tests`.
- [A10] No third-party dependency, no key in the repository, no machine-specific path under `backend/` — owned by `source-budget` and `no-secrets`.
- [A11] The plugin builds, loads, and the trending page renders on the real server — owned by `source`.
- [A12] Every new document is paired and the check catalogue still matches its generator — owned by `i18n` and `docs`.

## Risks

The one failure mode that can pollute the library is a placeholder name that diverges from the archive name: the two would become separate items. Criterion A7 pins them together, and the sanitising rule is duplicated on purpose between the plugin and `finalize.py`, so A7 is the only thing keeping the duplicate copy honest.

Whether Jellyfin creates a series item from a folder holding only a placeholder episode is unverified: the 159 empty series in this library are more likely residue than evidence, and the server's `SeriesResolver` only accepts a bare `tvshow.nfo` for a mixed-content library. The first delivery stage is the experiment that settles it, and a negative result downgrades series to watchlist-only while movies ship unchanged.

Whether the default version resolves to the real file is also unverified: Jellyfin plays the first entry of an alphabetically sorted version list, and the sort position of an unlabelled version has not been observed. A negative result activates the watcher-owned cleanup, the only path that would ever delete a placeholder.

Revisit this decision if Jellyfin's version ordering changes, or if the placeholder ever becomes the default for playback.

## Delivery stages

1. Probe — a throwaway `__jdl_probe__ (2026)` series folder and one movie folder settle whether a placeholder episode creates a series, and which version plays by default; both folders are removed afterwards, and a negative first result downgrades series to watchlist-only.
2. Backend — `backend/trending/`, its two routes, and the offline suite under `tests/backend_trending/`.
3. Plugin — settings, `trending/anchor`, `trending/library-status`, the placeholder resource, and the configuration-page group.
4. Client — the trending fragment, its styles, and the entry-deduplication change.
5. Records — the implemented note, README and AGENTS updates, and the paired documents.
