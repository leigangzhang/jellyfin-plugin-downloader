# Decision: A trending entry in the downloader plugin, with a followed title anchored into the Jellyfin library

English | [中文](2026-10-15-console-trending-anchor.zh.md)

Status: implemented
Date: 2026-10-15
Class: feature

## Problem

The plugin can only be entered from an item the library already knows: the detail-page button, or the header button's manual form. There is no way to act on a title that is not in the library yet, and nothing records "I want this" between seeing a title and downloading it. Three gaps, measured on this machine rather than assumed: no discovery surface (nothing in the plugin reached TMDb, and of the library's 170 series 159 held zero episodes); no wish state (the manual form was retyped every time); and followed titles had no home in the library (pages for undownloaded titles were built by hand, in one uniform shape). All 75 movies in `Movies/` were the same 8,307-byte placeholder MP4 (md5 `59563f02cff1daea13f1082b80fa695c`), each folder carrying `movie.nfo`, `poster.jpg` and `fanart.jpg`; cast arrived through `PeopleBaseItemMap` even though the NFO carries no `<actor>`.

## Decision

A second header entry, **热门**, lists TMDb trending and popular movies and series in an overlay and anchors a followed title into the library. It is `Jellyfin.Plugin.JellyfinDownloader/Web/inject_06_trending.js`, the sixth and last `ScriptSegments` fragment; its button carries its own class `.jdl-trending-btn` and its own icon `trending_up`, because `enforceSingleEntry()` in `inject_01_core.js` and `removeHeaderButton()` in `inject_02_entry.js` collapse every header node carrying `.material-icons.arrow_downward`. Picking a title calls the existing `openManualPanel({title, year, kind})`, so it lands in the scoring flow that already ships.

**Data.** `backend/trending/` holds `trending_source.py` (TMDb client over `urllib.request`, injectable opener), `trending_cache.py` (TTL cache keyed by `(language, list ids)`, 21600 s, atomic write through a temp file plus `os.replace`, stale fallback) and `trending_watchlist.py` (follow list, idempotent per `tmdb_id`). `GET /api/trending` and `POST /api/trending/watch` join `backend/console_server/http_api.py`; the data root is `media_download_lib.DATA_ROOT` (`JMD_DATA_DIR` can move it) and the package builds no path from `__file__`. `GET /api/trending` answers

```
{"lists":[{"id","label","items":[{"tmdb_id","title","original_title","year",
   "kind","poster_path","vote_average"}]}],
 "stale":<bool>, "guidance":null, "watched":[<tmdb_id>]}
```

with the four list ids `trending_movie_week`, `trending_tv_week`, `popular_movie`, `popular_tv`, and with `lists: []` plus a Chinese `guidance` string when `JMD_TMDB_API_KEY` is absent — in which case the route never reaches the network. `POST /api/trending/watch` answers `{"ok","exists","written"}`.

**Library state.** `POST /JellyfinDownloader/trending/library-status` answers "already in the library" for a batch of titles through `ILibraryManager` (normalised title plus `ProductionYear`, request order preserved); `POST /JellyfinDownloader/trending/anchor` writes the placeholder. Both live in the plugin rather than the backend: `MediaPaths.MediaRoot` and `ILibraryManager` are the only source of library paths, and `backend/` may not carry a machine-specific path. Both endpoints accept the platform's own kind vocabulary (`movie`/`film`, `series`/`episode`/`tv`/`show`/`variety`, `record`/`records`) and normalise it to the library category (`movie` → `Movies`, `series`/`episode`/`variety` → `TV Shows`, `record` → `Records`); a request without a 4-digit year is refused by `anchor` (`400 {"error":"year is required"}`) and never reports `exists: true` from `library-status`, because a year-less destination can never equal the real item's folder — the two would become separate library items.

**Placeholder.** `Web/placeholder.mp4` (8,307 bytes, md5 `59563f02cff1daea13f1082b80fa695c` — the file the library already used) ships as an `EmbeddedResource` and is written, after title sanitising and a `MediaPaths.MediaRoot` containment check, as

```
Movies/<title> (<year>)/<title> (<year>) - zz placeholder.mp4
TV Shows/<title> (<year>)/Season 01/<title> S01E01 - zz placeholder.mp4
```

through a same-directory temp file plus `File.Move`. An existing destination is reported and never rewritten. Two constraints force the shape: `atomic_same_volume_move` raises `FileExistsError` on an existing destination while `normalized_destination` puts the real file at exactly `<title> (<year>).<ext>`, so an unlabelled placeholder would break archiving whenever the download is an MP4; and Jellyfin treats files in one folder as versions only when the name begins character-for-character with the folder name before a version label, playing the first of an alphabetically sorted list — so the label must both separate it from the real name and sort last.

**Settings.** `PluginConfiguration` gains `TmdbApiKey` (empty), `TmdbLanguage` (`zh-CN`), `TrendingLists` (the four ids) and `AnchorEnabled` (`false`). `BackendManager.Start` injects `JMD_TMDB_API_KEY` only when the key is non-empty, and `GET /JellyfinDownloader/config` returns `tmdbApiKeyConfigured` rather than the key itself. The configuration page gains one 热门 group; `AnchorEnabled` off means zero writes to the library. `GET /JellyfinDownloader/config` also carries `guidance`, the overlay's single source for the no-key text; `GET /api/trending` keeps its own `guidance` field for direct API consumers.

**Not in this change.** No automatic download, no batch submit, no library row, no route hijack, no Seerr or *arr integration, and no deletion of placeholders. Cleanup, if it is ever wanted, belongs to the watcher stage.

## Alternatives considered

**Route the discovery through Seerr and an existing plugin.** `SeerrFin` (TMDb trending carousels, request modals) and `Home Screen Sections` (Jellyseerr Trending and Discover rows) both solve discovery, but both target ABI 12.0 while this server runs 10.11.11, and both require a Seerr instance that is not deployed here. They also end in Sonarr/Radarr, which this setup does not use.

**Read the trending lists off the configured resource stations.** Zero new credentials and the results are directly downloadable, but a station's "hot" page is only its own latest releases, it carries no poster, cast or rating, and every station redesign breaks it. It stays a possible second provider.

**Douban as the source.** No official API; scraping is unstable and gets the caller blocked. Rejected.

**Let the browser call TMDb directly.** Exposes the key to every client and breaks the existing same-origin proxy boundary. Rejected; the key stays in the plugin process, and the browser only ever sees `tmdbApiKeyConfigured`.

**A real route at `/web/#/jdl/trending`.** Needs either route hijacking or the Plugin Pages dependency; the overlay is smaller and keeps the plugin self-contained.

**Let the real file overwrite the placeholder at archive time.** `atomic_same_volume_move` refuses to overwrite an existing destination, and the user explicitly asked that archiving leave the placeholder alone, so the collision is avoided by naming instead.

**Delete the placeholder once the real file lands.** It would remove the second version entirely, but the user ruled it out; if the first version ever resolves to the placeholder during playback, the fallback is a cleanup owned by the watcher stage.

**Record follows in the watchlist only, without touching the library.** Cheapest, and it loses the whole point: a followed title would have no library page, no poster and no cast until the download lands.

## Consequences

- The plugin is reachable from titles the library does not hold: the header entry lists TMDb trending and popular lists and the manual scoring flow receives the picked title, year and kind. Following a title records it in `backend/trending` (watchlist) and, when `AnchorEnabled` is on, leaves a placeholder file in the media library.
- No third-party dependency was added: the TMDb client uses `urllib.request` only, the placeholder template is a resource, and the overlay uses no external script.
- The button's own class and icon are load-bearing: reusing `.jdl-header-btn`/`arrow_downward` would make the two header entries delete each other.
- Real TMDb traffic could not be verified in this delivery: outbound to `api.themoviedb.org` times out on this machine, so the live-list path is pinned only by an injected opener, and the live call still needs a machine that can reach TMDb plus a key in the configuration page. The no-key guidance path, the TTL, the stale fallback and the watchlist were all exercised without the network.
- `anchor` writes the placeholder file and creates its directories; the poster, cast and rating a followed title shows come from Jellyfin's own metadata providers, not from this change. A library whose providers are off will show the item with a filename-derived name and no artwork.
- `Jellyfin.Plugin.JellyfinDownloader/Controllers/JellyfinDownloaderController.cs` grew from 750 to 1,266 lines (30 KB → 50.8 KB) in this change and now exceeds the reader's 50 KB limit; `source-budget` covers `backend/*.py`, `tests/*.py` and `Web/*.js` but not `.cs`, so no gate goes red. Splitting the controller is left as a follow-up rather than folded into this change.
- A title whose TMDb record carries no year can be followed (watchlist) but is never anchored: with no year the overlay skips the anchor call instead of sending one the endpoint would refuse, so the card reads 仅记录 (`listed`) rather than surfacing a refusal.
- The one failure mode that can pollute the library is a placeholder name that diverges from the archive name: the two would become separate items. A7 pins them together, and the sanitising rule is deliberately duplicated between the plugin and `finalize.py`, so A7 is the only thing keeping the duplicate copy honest.
- Revisit this decision if Jellyfin's version ordering changes, if the placeholder ever becomes the default for playback, or if the placeholder ever has to be deleted or moved into the archive path.

## Testing

- [A1] With no key configured the overlay renders the `guidance` string that `GET /JellyfinDownloader/config` carries and the console stays clean: `backend/trending/trending_source.py` + `http_api.py` route pinned by `tests/backend_trending/test_backend_trending_routes.py`; the rendering half is the real-server check recorded in the delivery report. Owned by `source`, `tests`.
- [A2] `tests/backend_trending/test_backend_trending_source.py` — a fake opener returns >= 2 lists of >= 10 items, with the field shape above. Owned by `tests`.
- [A3] `tests/backend_trending/test_backend_trending_cache.py` — a second call inside the TTL does not reach TMDb, proven with an injected clock and an opener call count. Owned by `tests`.
- [A4] `tests/backend_trending/test_backend_trending_stale.py` — an unreachable TMDb returns the previous cache with `stale: true`. Owned by `tests`.
- [A5] Real-server acceptance in the delivery report (delivery stage 4): following a movie leaves `Movies/<title> (<year>)/<title> (<year>) - zz placeholder.mp4` on disk; the anchor path is also pinned at the unit level by `tests/backend_retired/test_backend_retired_trending_anchor_naming.py`. Owned by `source`.
- [A6] The same real-server acceptance: following a series leaves `Season 01/<title> S01E01 - zz placeholder.mp4`, and the library shows the series with one episode. Placeholder-episode series creation was confirmed on this machine before delivery rather than assumed. Owned by `source`.
- [A7] `tests/backend_retired/test_backend_retired_trending_anchor_naming.py` reads the controller's naming constants and asserts the placeholder path never equals `media_download_lib.normalized_destination`'s destination for the same title and year, for both movies and episodes. Owned by `tests`.
- [A8] `tests/backend_trending/test_backend_trending_check_exists.py` runs `backend/download/check_exists.py` as a subprocess against a temporary media root holding both the placeholder and a real file, and requires `ALREADY_EXISTS`; a placeholder-only directory still reports `NEEDS_DOWNLOAD`. Owned by `tests`.
- [A9] `tests/backend_trending/test_backend_trending_watchlist.py` pins that a repeated follow reports exists without rewriting the file; the anchor side (`AnchorEnabled` off means zero writes, an existing placeholder is never rewritten) is pinned by `tests/backend_retired/test_backend_retired_trending_anchor_naming.py` and the real-server acceptance. Owned by `tests`.
- [A10] `tests/backend_trending/test_backend_trending_no_machine_paths.py` rejects `__file__`-derived data paths, machine-specific absolute paths and third-party imports under `backend/trending/`; the repository-wide halves are the `no-secrets` and `source-budget` checks in `tools/check-invariants.py`. Owned by `source-budget`, `no-secrets`.
- [A11] Real-server acceptance in the delivery report: `./build.sh` and `./install.sh` succeed, the configuration page shows the 热门 group, `/JellyfinDownloader/script` carries the sixth fragment, and the overlay renders — through the browser tool against the running Jellyfin, with the trending lists served from the backend's own cache (seeded with one real-shaped entry) because outbound TMDb is unavailable on this machine. Owned by `source`.
- [A12] `python3 tools/gen-docs.py --check` and `python3 tools/pair-docs.py --check` are green with this record pair in place. Owned by `i18n`, `docs`.
