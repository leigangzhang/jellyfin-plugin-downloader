# Evidence record

Recorded: 2026-10-13T13:13:46Z
Scope: origin/main (merge base 898bcf8e4457)
Verdict: PASS — 5 passed, 0 failed, 0 could not run (of 5 runnable command(s))

## Changed paths and their surfaces

### i18n

A translated pair is two facts that must not drift. The answer key binds each section to a confirmed hash, the switcher keeps both sides reachable, and identical fenced blocks keep the code comparable. A gate proves the pair is complete and in step; only review proves it is faithful. Generated evidence records are excluded: pairing excludes them, so a change there must not ask for a pairing run.

- `.agents/notes/implemented/architecture/2026-10-13-ranking-flow-simplification.i18n.yaml`
- `.agents/notes/implemented/architecture/2026-10-13-ranking-flow-simplification.md`
- `.agents/notes/implemented/architecture/2026-10-13-ranking-flow-simplification.zh.md`
- `.agents/notes/implemented/architecture/2026-10-13-ranking-signals.i18n.yaml`
- `.agents/notes/implemented/architecture/2026-10-13-ranking-signals.md`
- `.agents/notes/implemented/architecture/2026-10-13-ranking-signals.zh.md`
- `.agents/notes/implemented/architecture/2026-10-13-ranking-sources.i18n.yaml`
- `.agents/notes/implemented/architecture/2026-10-13-ranking-sources.md`
- `.agents/notes/implemented/architecture/2026-10-13-ranking-sources.zh.md`

- **PASS (exit 0)** — `python3 tools/pair-docs.py --check`

  ```
  pair-docs: 25 pair(s) in scope, all complete and in step.
  ```

- **manual** — read one changed pair end to end: the counterpart must say what the authored side says, not merely something similar

### decisions

A decision without what it beat invites re-litigation.

- `.agents/notes/implemented/architecture/2026-10-13-ranking-flow-simplification.md`
- `.agents/notes/implemented/architecture/2026-10-13-ranking-flow-simplification.zh.md`
- `.agents/notes/implemented/architecture/2026-10-13-ranking-signals.md`
- `.agents/notes/implemented/architecture/2026-10-13-ranking-signals.zh.md`
- `.agents/notes/implemented/architecture/2026-10-13-ranking-sources.md`
- `.agents/notes/implemented/architecture/2026-10-13-ranking-sources.zh.md`

- **PASS (exit 0)** — `python3 tools/check-invariants.py`

  ```
  PASS evidence-record
  PASS notes-readme-budget
  PASS notes-readme-budget-zh
  PASS glossary-sections
  PASS plain-language-sections
  PASS reference-budget
  PASS reference-budget-zh
  PASS term-headings-ascii
  PASS banned-spellings
  PASS testing-budget
  PASS testing-budget-zh
  PASS tier-manifest
  ```

- **manual** — read the Alternatives considered section yourself; a gate cannot judge it

### prose

Prose states current state; links and budgets are checked mechanically. Generated evidence records are written by tools/run-evidence.py, so they are excluded here: they are not hand-authored prose, and pairing already excludes them.

- `.agents/notes/implemented/architecture/2026-10-13-ranking-flow-simplification.md`
- `.agents/notes/implemented/architecture/2026-10-13-ranking-flow-simplification.zh.md`
- `.agents/notes/implemented/architecture/2026-10-13-ranking-signals.md`
- `.agents/notes/implemented/architecture/2026-10-13-ranking-signals.zh.md`
- `.agents/notes/implemented/architecture/2026-10-13-ranking-sources.md`
- `.agents/notes/implemented/architecture/2026-10-13-ranking-sources.zh.md`
- `README.md`

- **PASS (exit 0)** — `python3 tools/check-invariants.py`

  ```
  PASS evidence-record
  PASS notes-readme-budget
  PASS notes-readme-budget-zh
  PASS glossary-sections
  PASS plain-language-sections
  PASS reference-budget
  PASS reference-budget-zh
  PASS term-headings-ascii
  PASS banned-spellings
  PASS testing-budget
  PASS testing-budget-zh
  PASS tier-manifest
  ```

- **manual** — read the changed page as its reader and follow every link; pair-docs already resolves links inside paired documents, but the unpaired pages (README.md, AGENTS.md, evidence records) have no link checker in this repository yet

### source

Behaviour changes need the smallest check that would fail for the regression. This repository has two runtimes: the Python backend under backend/ (syntax gate here) and the C# plugin that embeds Web/inject.js and Web/style.css (built by ./build.sh). There is no automated test suite yet, so a change to either runtime also owes the manual smoke path in AGENTS.md.

- `Jellyfin.Plugin.JellyfinDownloader/BackendManager.cs`
- `Jellyfin.Plugin.JellyfinDownloader/Configuration/BtSiteConfig.cs`
- `Jellyfin.Plugin.JellyfinDownloader/Configuration/PluginConfiguration.cs`
- `Jellyfin.Plugin.JellyfinDownloader/Configuration/configPage.html`
- `Jellyfin.Plugin.JellyfinDownloader/Controllers/JellyfinDownloaderController.cs`
- `Jellyfin.Plugin.JellyfinDownloader/Jellyfin.Plugin.JellyfinDownloader.csproj`
- `backend/build_console.py`
- `backend/candidate_score.py`
- `backend/console_server.py`
- `backend/pan_search.py`
- `backend/probe_magnets.py`
- `backend/search_pool.py`
- `backend/source_config.py`
- `backend/speed_probe.py`
- `tests/test_backend_integration.py`
- `tests/test_backend_probe.py`
- `tests/test_backend_retired_paths.py`
- `tests/test_backend_scoring.py`
- `tests/test_backend_search_budget.py`
- `tests/test_backend_sources.py`

- **PASS (exit 0)** — `python3 -m py_compile backend/*.py`
- **PASS (exit 0)** — `./build.sh`

  ```
    Determining projects to restore...
    All projects are up-to-date for restore.
    Jellyfin.Plugin.JellyfinDownloader -> /Users/ray/Workspace/jellyfin-plugin-downloader/Jellyfin.Plugin.JellyfinDownloader/bin/Release/net9.0/Jellyfin.Plugin.JellyfinDownloader.dll
    Jellyfin.Plugin.JellyfinDownloader -> /Users/ray/Workspace/jellyfin-plugin-downloader/out/
  built -> /Users/ray/Workspace/jellyfin-plugin-downloader/out
  ```

- **manual** — run the plugin against a real Jellyfin (./install.sh stops the server first, then open a detail page and confirm the console entry still works); backend/ has no automated tests yet, so this manual smoke path is the only behaviour evidence

### packaging

Manifests and lockfiles change the shipped artifact, not just the source. meta.json, manifest.json, the csproj <Version> and icon.png are the release surface of this plugin: a change here changes what Jellyfin and the plugin repository hand to users.

- `Jellyfin.Plugin.JellyfinDownloader/Jellyfin.Plugin.JellyfinDownloader.csproj`
- `meta.json`

- **PASS (exit 0)** — `./build.sh`

  ```
    Determining projects to restore...
    All projects are up-to-date for restore.
    Jellyfin.Plugin.JellyfinDownloader -> /Users/ray/Workspace/jellyfin-plugin-downloader/Jellyfin.Plugin.JellyfinDownloader/bin/Release/net9.0/Jellyfin.Plugin.JellyfinDownloader.dll
    Jellyfin.Plugin.JellyfinDownloader -> /Users/ray/Workspace/jellyfin-plugin-downloader/out/
  built -> /Users/ray/Workspace/jellyfin-plugin-downloader/out
  ```

- **UNFILLED** — `build with ./build.sh, then install the built plugin with ./install.sh (it stops Jellyfin first) and confirm the plugin loads and the version in meta.json matches the <Version> in the csproj` (a placeholder is not evidence)

### agent-inputs

Anything an agent reads is an input to a model request, so it has to be enumerable and its edits reviewed as behaviour. A skill whose description stops naming when it loads has been silently deleted from the agent's reach.

- `README.md`

- **PASS (exit 0)** — `python3 tools/check-invariants.py`

  ```
  PASS evidence-record
  PASS notes-readme-budget
  PASS notes-readme-budget-zh
  PASS glossary-sections
  PASS plain-language-sections
  PASS reference-budget
  PASS reference-budget-zh
  PASS term-headings-ascii
  PASS banned-spellings
  PASS testing-budget
  PASS testing-budget-zh
  PASS tier-manifest
  ```

- **manual** — re-read what the agent actually sees: the description, the section order, the examples. A renamed section or a reworded trigger is a changed input, not an edit.

### tests

A test is evidence only when its command and its failure mode are named. A test nobody runs, or one that cannot fail, proves nothing.

- `tests/fixtures/jmd_scoring/speed_cases.json`
- `tests/test_backend_integration.py`
- `tests/test_backend_probe.py`
- `tests/test_backend_retired_paths.py`
- `tests/test_backend_scoring.py`
- `tests/test_backend_search_budget.py`
- `tests/test_backend_sources.py`

- **PASS (exit 0)** — `python3 -m unittest discover -s tests -v  # this kit's own suite; replace with your project's test command`

  ```
  test_a_surface_without_exclude_still_claims_the_path (test_records.SurfaceExclude.test_a_surface_without_exclude_still_claims_the_path) ... ok
  test_an_excluded_path_is_not_claimed_by_that_surface (test_records.SurfaceExclude.test_an_excluded_path_is_not_claimed_by_that_surface) ... ok
  test_an_ordinary_document_is_still_claimed (test_records.SurfaceExclude.test_an_ordinary_document_is_still_claimed) ... ok
  test_a_stage_declared_none_with_files_present_is_rejected (test_tiers.TierSwitch.test_a_stage_declared_none_with_files_present_is_rejected) ... ok
  test_a_stage_that_owns_several_paths_is_checked_at_each_one (test_tiers.TierSwitch.test_a_stage_that_owns_several_paths_is_checked_at_each_one)
  `home` may be a list: an absent stage must own none of the paths it lists. ... ok
  test_a_tier_table_that_disagrees_is_rejected (test_tiers.TierSwitch.test_a_tier_table_that_disagrees_is_rejected) ... ok
  test_agreement_in_both_directions_is_accepted (test_tiers.TierSwitch.test_agreement_in_both_directions_is_accepted) ... ok
  test_an_installed_stage_with_no_files_is_rejected (test_tiers.TierSwitch.test_an_installed_stage_with_no_files_is_rejected) ... ok
  ----------------------------------------------------------------------
  Ran 261 tests in 34.453s
  OK (skipped=2)
  ```

- **manual** — run the owning test for each changed test file, and watch it fail for the regression it pins
- **manual** — read docs/testing.md: judge whether the tier, the determinism rules, and the negative control still hold

## Paths with no declared surface

- `"JMD\346\223\215\344\275\234\346\265\201\347\250\213\345\233\276.html"`
