# Jellyfin Downloader: download pipeline (module level)

English | [中文](download-flow.zh.md)

The pipeline runs in three layers and 13 stages, from the Jellyfin detail page to a normalized file in the media library. The stage-by-stage map, the diagrams and the failure branches live in [download-flow.html](download-flow.html) — that page is the single home for the narrative. This page carries the facts a check can verify: the module inventory, the flow's budgets, and what is intentionally kept.

## The pipeline in one line

`entry → same-origin proxy → build search job → duplicate check → magnets and cloud shares → probe scoring → snapshot and panel polling → submit → download watch → validation and archive → library refresh`

## Module inventory

| Layer | Module | What it does |
|---|---|---|
| Plugin (C#) | `Controllers/JellyfinDownloaderController.cs` | injected fragments, config, season list, backend lifecycle, `/api/**` proxy |
| Plugin (C#) | `BackendManager.cs` | starts the backend, walks the port range, verifies `SERVICE_ID`, injects `JMD_*` |
| Plugin (C#) | `MediaPaths.cs` | resolves the media root and the same-volume `.staging` |
| Plugin (C#) | `Web/inject_0*.js` (5 fragments) | entry button, panel, search request, candidate rendering |
| Backend | `console_server.py` + `console_server/` | HTTP entry, contract constants, routing, search jobs, snapshots, subprocess runner |
| Backend | `search/` + `torrents_csv_source.py` | resource-site ladder and the torrents-csv second channel |
| Backend | `pan/pan_search.py`, `pan/pan_pool.py` | cloud-share links and cloud candidates for the same scoring pass |
| Backend | `pan/pan_transfer.py` | cloud retrieve state machine: `plan` / `verify-transfer` / `set-save-path` / `verify-retrieve` / `watch` / `status` |
| Backend | `probe/` + `candidate_score/` | health, metadata, weighted score, ranking |
| Backend | `download/` | duplicate check, submit to Thunder, watcher, six-layer verification, issue handling, cleanup |
| Backend | `media_download_lib/` | Thunder DB reads, naming, atomic same-volume move, finalize |

## Search states and exit codes

`console_server.py` maps probe output to a state name; the same tokens appear in the job log.

| State | Meaning |
|---|---|
| `ok` | the round delivered a candidate |
| `best_below_min_score` | ranked, but the top candidate is below the threshold |
| `best_dead_swarm` | the top candidate is a dead swarm; delivering it would fail to parse |
| `swarm_dead` | no metadata and no external seeders: the torrent is dead |
| `metadata_failed` | no metadata but external peers exist: a network or tracker problem |
| `no_eligible_resource` | every candidate was rejected structurally |
| `probe_partial` | the soft budget ran out; the result is incomplete |
| `rc_N` | the script exited non-zero (for example `rc_2` when the station config is bad) |

## Redundant and kept

Findings from a read-only audit. Rows that say "removed" are gone from the tree; the rest are deliberately kept.

| Item | Verdict |
|---|---|
| `media_download_lib/paths.py` `thunder_parallelism()` | dead: no reference outside its own file; removed |
| `/api/pan/*` + `pan_transfer.py` | kept on purpose: the cloud retrieve path stays |
| `/api/cleanup`, `/api/mark`, publish endpoints | reachable only from the backend itself; no client in this repository |
| `data-jdl-header`, `data-jdl-key` | write-only attributes with no reader; removed |
| `/python/status`, `/python/install` | orphaned endpoints; the configuration page no longer offers the install entry; removed while `PythonInstaller.cs` stays as the documented install guide |
| `BackendBaseUrl`, `BackendScript` | kept: the only way to point the plugin at another backend |
| `BackendManager.Status()` extra fields | external JSON contract; no reader inside this repository |

## Unverified

- Whether the panel's minimum-score threshold is written back to the plugin configuration.
- The real wall-clock cost of one probe round: `console_server.py` states the figure is derived, not measured on this machine.
- Whether anything outside this repository consumes the `/api/mark`, `/api/cleanup` and `Status()` fields.

Recorded at commit 2d7da69. Visual source of truth: [download-flow.html](download-flow.html).
