# Decision: Console configuration page — four groups of three lines, one address per station, and the port row owning the backend

English | [中文](2026-10-13-console-config-page-redesign.zh.md)

Status: implemented
Date: 2026-10-13
Class: feature

## Problem

The plugin's configuration page asked the operator for things the plugin could derive or compose, and showed back what was already on screen. Round 1 named three symptoms: fonts and styles that did not match the console's other plugin pages, elements spaced so loosely that the page read as empty, and three rows that should not be there — the resolved media root (`当前解析到 …`), the read-only staging directory, and the backend line `正在运行 · pid … · 插件管理`.

One screen carried seven groups: backend service, port, Python interpreter, media directory, staging directory, a station row of four fields each, and a keyword textarea. A station's four fields put the address — the only part the operator has an opinion about — in the least visible position; every group carried its own line height; and the page's own CSS invented typography the console does not use, a `code` tag and a monospace media-root echo among them.

The page also offered a Python-interpreter field and an install entry, while the prerequisite those served — a Python 3.9+ interpreter on the machine — was written down nowhere the operator would look.

## Decision

1. [configPage.html](../../../../Jellyfin.Plugin.JellyfinDownloader/Configuration/configPage.html) is four groups in this order — 媒体目录 → 资源站点 → 关键词组合 → 服务端口 — each shaped as a section title, the input or list it owns, and one description line. The seven-group screen is gone.
2. A station is one address. The page shows and accepts the station as the operator names it; saving composes the API path (`/api/search`, `/service/search`) into the stored value, and loading folds a known default path back to the bare station. An address carrying `{q}` is a cloud-search page and keeps its own entry. A host the plugin does not recognise is refused at save rather than stored under a guessed kind.
3. Stations and keywords share one list interaction: a row per entry, up / down / delete at the row's end, `+` on the last row only, every row deletable including one just added, and an emptied list reseeds a single blank row.
4. The media directory is the only path the operator types; empty means the plugin resolves it from the Jellyfin library root and shows that as the placeholder. Staging is fixed at that root's `.staging` and rendered read-only.
5. The page's style is the console's own: `.sectionTitle` for group titles, `.emby-input` for inputs, `.fieldDescription` for descriptions, `button-alt` for actions, the theme's icon buttons for row controls. The page's own CSS keeps only layout the theme has no class for. Where a size has to match the theme, it reuses the theme's element and class instead of copying the number — the port label is an `h2.sectionTitle`, not a `1.5em`.
6. The port row displays `host:port`, a status badge (运行中 / 已停止 / 启动中), 刷新状态 and 重启后端. It has no port input and renders no pid. The port is settled at start — the configured 8123, or the next free one. Saving restarts the backend, because that is how the new configuration reaches it.
7. Backend ownership is scoped twice: the plugin stops only the process its own pidfile records, never whatever happens to listen on the port, and it recognises its backend by the `service` marker in the JSON response, not by "something answers on this port".
8. Defaults live in the plugin, not in the page's markup: two built-in stations stored as bare stations (`https://m.diao.im`, `https://torrents-csv.com`) and three keyword rows of one word each (`高清剧集网`, `DDHDTV`, `BBHDTV`). An empty configuration renders them instead of blank fields, and the C# and Python copies are asserted word-for-word identical. The Python interpreter is not a configuration item: its field and install entry are gone, and the prerequisite with its per-platform commands lives in [README.md](../../../../README.md) 快速开始 step 0, worded in [PythonInstaller.cs](../../../../Jellyfin.Plugin.JellyfinDownloader/PythonInstaller.cs).

Why stations and keywords became plugin configuration at all is a separate decision, recorded in [ranking-sources](../architecture/2026-10-13-ranking-sources.md) and [ranking-flow-simplification](../architecture/2026-10-13-ranking-flow-simplification.md).

## Alternatives considered

**Polish the seven-group screen instead of replacing it.** It was the page's existing shape, so the diff would have been small. It lost because the layout, not the styling, was the problem: a station row's four fields buried the address, each group set its own rhythm, and no amount of restyling makes a four-field row read as one station. The redesign replaced the layout (`5f97c8c`).

**Require the full API address in the page.** Then what the operator types is exactly what is stored, with no composition to trust. It lost because the API path is the plugin's knowledge, not the operator's: the built-in stations are known as bare hosts, and a mistyped path yields a station that looks configured and never answers. The page now composes on save and folds known paths back on load (`5574fd8`).

**Stop whatever holds the port.** The first implementation ran `lsof` and killed the listener on the configured port. It lost because it never checks ownership: an unrelated service on 8123 would have been killed, and the intended increment only happened when the kill failed. Replaced by the pidfile-scoped stop plus `ResolvePort` (`c59cc5d`).

**Decide "our backend is running" by "the port answers".** It lost twice over: a foreign HTTP service answering 404 or 2xx JSON on 8123 made the plugin skip starting its own backend, and the first fix — treat anything under 500 as ours — still could not tell the two apart. The backend now stamps a `service` marker on every JSON response and the plugin parses it (`f801a37`).

**Keep a port the operator can edit.** The page used to take a port value. It lost because the port's identity is where the backend actually listens, which is settled at start when the configured one is taken — an editable field would sit next to a badge that disagrees with it. The row became display-only (`5f97c8c`, `0432907`).

**Colour the status text instead of filling the badge.** Lost on measurement, not taste: green `#2e9e4f` scores 3.43:1 and amber `#d79b00` 2.45:1 against white, under AA; no single colour clears 4.5:1 on both a white and a near-black page. The filled pill carries its own 4.72–6.05:1 (`f413ecf`).

**Keep one keyword row listing all three words.** That was the round-1 template value and it shipped first. It lost on measurement: the index ANDs the words of one query, so `漫长的季节 高清剧集网 DDHDTV BBHDTV` returns 0 rows for every title while the same query with `BTHDTV` returns 10. The operator chose three rows of one word each, and the measurement stayed in `backend/source_config.py` as the reason (`12eec0b`, `f7068cb`).

**Keep the Python interpreter field and its install entry.** Lost because the page's job is what the plugin derives or owns: an interpreter is a property of the machine, and the field's only purpose was to overrule autodetection. The steps moved to the README with their wording owned by `PythonInstaller.cs`, and the removed labels became a ban so no document promises them again (`5f97c8c`, `fc46e2d`).

## Consequences

- The page is now the console's page: three theme classes carry the typography, the page's own CSS only holds the list and port layout, and a group title, a description, and an input line up with the console's other plugin pages.
- The stored station value is the full API address while the page speaks in station names. That is what makes the built-in defaults legible, and it means a change in an index's API path is a plugin-side change, not an operator's.
- A dead backend is recoverable from the page: the badge reads 已停止 and 重启后端 brings it back. Verified by killing the process and reading the row (`1978169`).
- The port's truth is one value. The proxy reads `BackendManager.BaseUrl`, so an incremented port reaches the panel; before that fix the whole panel 502'd on an auto-incremented port, which had quietly cancelled the increment feature end to end (`f8e22d2`).
- Saving restarts the backend. Any job it is running is interrupted; that is the price of configuration arriving as environment.
- Three keyword rows mean three queries per title against the search budget, and `BBHDTV` yields 0 rows on the measured index — a cost the operator accepted deliberately, with the caveat written next to the default.
- The console now depends on theme classes and variables. A theme that renames `.sectionTitle` or drops `paper-icon-button-light` changes this page's look without touching it; the pool of theme names is the coupling this decision accepted over a self-contained stylesheet.
- Three defects were found in this work's own first versions and fixed with guards: the config endpoint returned 500 because one anonymous object held both `btSites` and `BtSites` (`System.Text.Json` compares case-insensitively, and neither the compiler nor the unit tests noticed); a media root containing a space was cut by the shell so the backend silently received `/Volumes/XIAOMI` instead of the real path; and the backend inherited Jellyfin's entire environment, 75 variables including plaintext credentials, now cleared and whitelisted to 22 with none credential-shaped (`550d3a4`, `c284520`, `203a081`).
- Legacy configuration keeps working: a dual-entry station is no longer misread as a cloud page, an unknown host without a declared kind is refused instead of defaulting to the built-in one, and a self-built station's kind now round-trips through a save instead of being rewritten on the operator (`c59cc5d`, `f801a37`, `db85433`).

## Testing

- [C1] `tests` — [test_backend_retired_config_page.py](../../../../tests/backend_retired/test_backend_retired_config_page.py) carries the page's source-side guards (53 assertions): the removed rows stay removed, the port row renders the three states and never a `status.pid`, the media field's width is computed from the shared `--jdl-actions-width` variable so it cannot drift from the lists, the badge palette is computed against WCAG AA inside the test, the keyword default contains no space, and the C# and Python defaults are equal word for word.
- [C2] `source` — the behaviour behind those guards was verified against a running instance and recorded in the commits: saving writes the full API addresses and restarts the backend (`550d3a4`), killing the backend makes the row read 已停止 and the restart button brings it back (`1978169`), an incremented port reaches the panel through the proxy (`f8e22d2`), and a media root containing a space arrives whole (`c284520`).
- [C3] `source` — the backend side is pinned in the same suite: the `service` marker on every JSON response, the pidfile written at start and removed at exit, `JMD_PORT` as the port's only channel, and `Environment.Clear()` plus the whitelist in [BackendManager.cs](../../../../Jellyfin.Plugin.JellyfinDownloader/BackendManager.cs).
- [C4] `tests` — two static scans keep the C# side honest: every anonymous object in the plugin source is checked for keys that differ only by case, and the two copies of the defaults are compared with each other.
- [C5] `docs` — [README.md](../../../../README.md) describes the page as it now is (four groups, the station address model, the three keyword rows, 快速开始 step 0 for the interpreter), and the `banned-spellings` check rejects the removed install-entry labels, so the documentation cannot promise a control the page does not have.
