# Decision: Ranking flow — a configurable station list, publisher keywords first, and no speed probe

English | [中文](2026-10-13-ranking-flow-simplification.zh.md)

Status: implemented
Date: 2026-10-13
Class: architecture

## Problem

Three parts of the pool were decided in code rather than configuration, and one stage
earned nothing for what it cost.

The stations were constants (`search_pool.DEFAULT_BASE_URL`, `DEFAULT_EXTRA_SOURCES`) and
so was the publisher keyword (`PUBLISHER_QUERY_TOKEN = "DDHDTV"`), gated on the title
containing ASCII under the "don't mix Chinese and English" rule. Anyone who used a
different pair of stations, or wanted a different publisher signature, had to edit the
source. The channels were a fixed ladder — structured API, then a browser-discovered
magnet file, then a local Pansou instance — and the cloud-drive half had no entry other
than that local instance.

The speed probe was still wired: `speed_probe.py` measured a ten-second aria2c window at
17.6–24.1 s per candidate, and its repeat spread on the same info hash was 1.16–8.50×,
so it could not order the list it was paid for (the predecessor records measured `ρ =
−0.214` across hashes). The console stopped passing it, but the module, the flags, the
`measure_speeds` stage and the `/api/verify` endpoint that called it all remained — and
no UI called that endpoint.

## Decision

Configuration moved into one home, the search became a loop over stations with the
publisher keywords first, and the retired paths were deleted rather than disabled.

1. **`backend/source_config.py` owns both lists.** `JMD_BT_SITES` (a JSON array of
   `{name, kind, base_url, web_search_url}`) and `JMD_L1_KEYWORDS` (an array of query
   suffixes). A station needs at least one entrance: `base_url` + `kind` for magnets
   (`kind` selects the adapter — `diao` for the structured search API, `torrents-csv` for
   the aggregate index), and/or `web_search_url` for the cloud channel. `web_search_url`
   may stand alone — the kind of site that publishes cloud shares and has no search API is
   exactly the cloud channel's usual entrance, and it is normalised to `kind: ""` so the
   magnet side never treats it as a station. Empty means the built-in defaults: the two
   stations already in use, and the two publisher signatures that were measured to work. A
   malformed list is reported (`SOURCE_CONFIG_ERROR`) and search exits 2; it is never
   silently replaced by the defaults.
2. **Every configured station is asked, and the budget is divided.** The per-keyword wall
   clock is split evenly across the API-kind stations, with the same treatment for the
   non-API ones inside their reserved slice. The worst case therefore stays
   `--max-queries × --page-budget-secs` whatever the station count, which is what keeps
   the console's `12 × 36 s ≤ 450 s` arithmetic true. Stations are capped at 8 and keyword
   combinations at 8.
3. **The configured keywords are the `anchor` tier, and it goes first.** `title +
   keyword` for every keyword, ahead of alias/year/title, and it runs under
   `--strict-match` too. The default keywords are the two that were measured to work:

   | query on `m.diao.im` (2026-10-13) | hits |
   |---|---|
   | `漫长的季节 DDHDTV` | 6/6 that publisher |
   | `漫长的季节 高清剧集网` | 10/10, covering DDHDTV and BPHDTV |
   | `漫长的季节 BTHDTV` | 10/10 |
   | `漫长的季节 高清剧集发布网` | 0 |
   | `漫长的季节 BBHDTV` | 0 |
   | `周三俱乐部 第二季 DDHDTV` | 0 |

   The last three lines are why the defaults are not the phrase that reads like the
   publisher's name, and why the anchor tier is not combined with a season hint.
4. **The cloud channel is each station's `web_search_url`.** A station that declares one
   (`.../search?q={q}`, required to contain `{q}`) is fetched through the existing
   `pan_search.py --page` path, and the console says so per station. No station declaring
   one prints `云盘通道没有入口` instead of returning a silent empty. A magnet search whose
   station list has no `base_url` at all says `NO_MAGNET_STATION` and exits 4 rather than
   reporting an empty pool.
5. **Deleted, not disabled:** `backend/speed_probe.py`, `probe_magnets.measure_speeds`
   and the `--speed-probe*` flags, `console_server`'s `/api/verify` and its import,
   `probe_magnets --allow-pansou` / `--allow-pansou-fallback`, `pan_search --pansou`,
   `probe_magnets --magnet` / `--info-hash`, `build_console.rescore_entry`, and
   `search_pool --base-url` / `--extra-source` / `--extra-source-base-url`.
   `candidate_score._speed_score` keeps four modes — `seeds`, `seeds-external`,
   `unknown`, `dead-swarm` — and no longer reads `measured_speed_mbps`.

## Alternatives considered

**Keep the local Pansou instance as the cloud channel.** Declined: it needed a service
running on `127.0.0.1:64056` that the plugin cannot install, and the user asked for one
cloud entry, not two.

**Give each station its own keyword list.** Declined: the keywords are publisher
signatures in the release name, not properties of a station, and the user chose two
independent global lists.

**Keep the speed probe behind a flag as a manual diagnostic** (what the predecessor
record chose). Declined: nothing in the panel called it, the one endpoint that did
produced a number that could not order candidates, and a flag nobody passes is a path
nobody tests.

**Ship a default `web_search_url` for the two built-in stations.** Declined:
`torrents-csv.com` answers a page fetch with a bot-check page and `m.diao.im` is a magnet
index, so the default would make the cloud channel look configured while returning
nothing. An inert channel that says why is better than a fabricated entry.

**Query the anchor tier as `title + season hint + keyword` so strict season still
holds.** Declined: measured 0 hits — the index ANDs the words, and the season hint is not
in the release name in a form this index matches.

## Consequences

The default round is now two stations, two anchor queries per name, no speed probe; the
cloud channel stays inert until a station declares a web page. The anchor tier carries no
season marker, so strict-season searches *would* see the show's other seasons; an explicit
season-marker filter closes that (`candidate_seasons` / `strict_season_filter`), and it
recognises the three forms a release name actually uses — `SxxEyy`, a bare `Sxx`,
`第N季`/`第X季` and `Season N` — as a union. A candidate that states no season is kept
rather than guessed at, because dropping a whole-season pack is harder to notice than
leaking one show. A station list longer than one divides the same
budget into smaller slices, so each station's own recall drops. A `1.0.0.7` configuration
XML has none of these keys and keeps working: an absent list means "use the backend's
built-in defaults". A station with only a `web_search_url` contributes nothing to the
magnet channels and costs no budget slice — it is an entry for the cloud channel alone. Pan candidates parsed from a web page carry the bare title as their
name (`extract_share_entries` records the link, not the release name), so their quality
score is coarser than the removed Pansou path's, which read the search result's `note`;
that is a known gap, not a contract.

## Testing

- [B1] `tests` — `tests/test_backend_retired_paths.py` is the new guard. Its AST scan goes
  red on a retired symbol, keyword-argument name or CLI-flag constant reappearing anywhere
  under `backend/`; it also pins `speed_probe.py`'s absence, the console's missing
  `/api/verify`, and the `source_config` contract (defaults, validation, caps, `{q}`
  requirement, `web_search_urls`). Its `CloudChannelWiringTests` drives a real
  `console_server._run_search_job` round with the CLI runners replaced, so the cloud
  channel is pinned by behaviour rather than by a source-string assertion: two stations
  with web pages produce one `pan_search.py` call carrying both `--page` arguments with the
  title URL-encoded, and a station list with none produces no call at all plus the
  `云盘通道没有入口` line.
- [B2] `tests` — `tests/test_backend_sources.QueryLadderTests` pins the anchor tier's
  position, configured order, CJK coverage and strict-mode presence;
  `run_search_main`/`EndToEndOfflineTests` pin the env-driven station list, the
  env-driven keywords and the `SOURCE_CONFIG_ERROR` exit.
- [B3] `tests` — `tests/test_backend_scoring` pins that stale `measured_speed_mbps` /
  `speed_unmeasured` fields change neither the score nor the breakdown, and the golden
  table (`tests/fixtures/jmd_scoring/speed_cases.json`) was rewritten for the four
  surviving modes.
- [B4] `tests` — `tests/test_backend_probe.ConsoleWiringTests` and
  `tests/test_backend_integration` keep the console from passing a speed switch and keep
  the probe's offline dry run green.
- [B5] `source` — `python3 -m py_compile backend/*.py` plus `./build.sh`, because the
  configuration surface changed on both sides of the plugin/C# boundary.
- [B6] `docs` — README's feature table, configuration table and developer notes were
  updated in the same change, and both predecessor records link here.
- [B10] `tests` — the two high-severity findings from the review round: a round whose
  non-API stations cannot produce a candidate file no longer hands the *previous* round's
  `/tmp/<slug>.magnets` to the probe (`CloudChannelWiringTests` pins both directions), and
  the probe no longer aborts before reading `--extra-candidates` when that file is missing
  (`MissingCandidateFileTests`), so cloud candidates survive a magnet-side failure.
- [B11] `tests` — the strict-season filter is pinned in both error directions:
  `SeasonDetectionTests` covers the three explicit forms and the numerals
  (`cn_number`), `StrictSeasonFilterTests` drops another season for `Sxx` and for `第二季`,
  and asserts that a candidate with no season marker (a pack, a bare title, `第11集`) is
  kept. Both were watched red: removing the word forms fails the detection cases, and
  treating "no marker" as "another season" fails the keep cases.
- [B9] `tests` — the cloud-only station contract: `source_config` accepts a station with
  a `web_search_url` and no `base_url` (and rejects one with neither entrance), and
  `magnet_sites()` keeps it out of both magnet channels. `tests/test_backend_sources` pins
  the two consequences: such a station is never queried by the non-API channel, and a
  station list with no `base_url` at all prints `NO_MAGNET_STATION`, makes no request and
  exits 4.
- [B8] `source` — one live round on 2026-10-13 (real index, real swarm, no fakes):
  `search_pool.py 漫长的季节 --publisher-only` ran the two configured keywords as its only
  queries (`[anchor] 漫长的季节 高清剧集网` → 61 hits in 5.4 s over 7 pages, stopped by
  `--enough`), the publisher name filter kept all 61, and `probe_magnets.py` over the six
  largest of them reported `stage_seconds = {health, metadata, live_check}` — no speed stage
  — with a `speed` breakdown of `{seeds_prior, seed_source, seeds, peers, mode,
  score_source}` and `live_check.reused_from_enrich = 1` (the re-score path). Best score
  72.38 with a magnet present. Recorded because the offline suite cannot see the real index.
- [B7] `packaging` — both new configuration keys were round-tripped through
  `System.Xml.Serialization.XmlSerializer` against the built plugin assembly (the serializer
  Jellyfin uses for `BasePluginConfiguration`): two stations carrying all four fields and two
  keyword combinations survive serialize → deserialize intact, and an XML with neither key
  deserializes to empty lists — which is what makes "an absent list means the backend's
  built-in defaults" true for an existing `1.0.0.7` install. Reproduce with a throwaway
  console project referencing this csproj plus `Jellyfin.Controller`/`Jellyfin.Model`
  10.11.11 and serializing a `PluginConfiguration` with both lists populated. The same
  harness invoked the controller's shape check directly (reflection): three stations
  including a cloud-only one round-trip intact, and the check accepts a station carrying
  only a `web_search_url` while rejecting one with neither entrance, an unknown `kind` next
  to a `base_url`, and a `web_search_url` without `{q}`. Not a permanent guard: this
  repository has no C# test project.
