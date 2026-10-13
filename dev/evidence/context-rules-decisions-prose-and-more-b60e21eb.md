# Evidence record

Recorded: 2026-10-13T02:30:04Z
Scope: origin/main (merge base b60e21ebb3f5)
Verdict: PASS — 7 passed, 0 failed, 0 could not run (of 7 runnable command(s))

## Changed paths and their surfaces

### context-rules

Always-loaded rules are a budget; they must stay short and well-formed.

- `.agents/notes/AGENTS.md`
- `AGENTS.md`
- `CLAUDE.md`
- `tests/AGENTS.md`
- `tools/AGENTS.md`

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


### decisions

A decision without what it beat invites re-litigation.

- `.agents/notes/AGENTS.md`
- `.agents/notes/README.md`
- `.agents/notes/README.zh.md`
- `.agents/notes/implemented/TEMPLATE.md`
- `.agents/notes/implemented/TEMPLATE.zh.md`
- `.agents/notes/proposed/TEMPLATE.md`
- `.agents/notes/proposed/TEMPLATE.zh.md`

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

- `.agents/notes/AGENTS.md`
- `.agents/notes/README.md`
- `.agents/notes/README.zh.md`
- `.agents/notes/implemented/TEMPLATE.md`
- `.agents/notes/implemented/TEMPLATE.zh.md`
- `.agents/notes/proposed/TEMPLATE.md`
- `.agents/notes/proposed/TEMPLATE.zh.md`
- `.agents/skills/README.md`
- `.agents/skills/README.zh.md`
- `.agents/skills/apply-distrust/SKILL.md`
- `.agents/skills/cut-release/SKILL.md`
- `.agents/skills/define-acceptance/SKILL.md`
- `.agents/skills/engineer-tests/SKILL.md`
- `.agents/skills/land-changes/SKILL.md`
- `.agents/skills/measure-perf/SKILL.md`
- `.agents/skills/migrate-and-retire/SKILL.md`
- `.agents/skills/pick-evidence/SKILL.md`
- `.agents/skills/plain-language/SKILL.md`
- `.agents/skills/record-decision/SKILL.md`
- `.agents/skills/review-standard/SKILL.md`
- `.agents/skills/shape-intent/SKILL.md`
- `.agents/skills/trim-prose/SKILL.md`
- `.agents/skills/write-docs/SKILL.md`
- `AGENTS.md`
- `CLAUDE.md`
- `dev/README.md`
- `dev/README.zh.md`
- `dev/capabilities/TEMPLATE.md`
- `dev/capabilities/TEMPLATE.zh.md`
- `dev/contracts/TEMPLATE.md`
- `dev/contracts/TEMPLATE.zh.md`
- `dev/contracts/kinds.md`
- `dev/contracts/kinds.zh.md`
- `dev/review/TEMPLATE.md`
- `dev/review/TEMPLATE.zh.md`
- `docs/README.md`
- `docs/README.zh.md`
- `docs/adopting.md`
- `docs/adopting.zh.md`
- `docs/check-catalog.md`
- `docs/documentation.md`
- `docs/documentation.zh.md`
- `docs/evolution.md`
- `docs/evolution.zh.md`
- `docs/extending.md`
- `docs/extending.zh.md`
- `docs/glossary.md`
- `docs/glossary.zh.md`
- `docs/i18n.md`
- `docs/i18n.zh.md`
- `docs/kit.md`
- `docs/kit.zh.md`
- `docs/plain-language.md`
- `docs/plain-language.zh.md`
- `docs/testing.md`
- `docs/testing.zh.md`
- `tests/AGENTS.md`
- `tests/README.md`
- `tests/README.zh.md`
- `tools/AGENTS.md`
- `tools/README.md`
- `tools/README.zh.md`

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

- **UNFILLED** — `<your Markdown link/lint check>` (a placeholder is not evidence)

### i18n

A translated pair is two facts that must not drift. The answer key binds each section to a confirmed hash, the switcher keeps both sides reachable, and identical fenced blocks keep the code comparable. A gate proves the pair is complete and in step; only review proves it is faithful. Generated evidence records are excluded: pairing excludes them, so a change there must not ask for a pairing run.

- `.agents/notes/AGENTS.md`
- `.agents/notes/README.i18n.yaml`
- `.agents/notes/README.md`
- `.agents/notes/README.zh.md`
- `.agents/notes/implemented/TEMPLATE.i18n.yaml`
- `.agents/notes/implemented/TEMPLATE.md`
- `.agents/notes/implemented/TEMPLATE.zh.md`
- `.agents/notes/proposed/TEMPLATE.i18n.yaml`
- `.agents/notes/proposed/TEMPLATE.md`
- `.agents/notes/proposed/TEMPLATE.zh.md`
- `.agents/skills/README.i18n.yaml`
- `.agents/skills/README.md`
- `.agents/skills/README.zh.md`
- `.agents/skills/apply-distrust/SKILL.md`
- `.agents/skills/cut-release/SKILL.md`
- `.agents/skills/define-acceptance/SKILL.md`
- `.agents/skills/engineer-tests/SKILL.md`
- `.agents/skills/land-changes/SKILL.md`
- `.agents/skills/measure-perf/SKILL.md`
- `.agents/skills/migrate-and-retire/SKILL.md`
- `.agents/skills/pick-evidence/SKILL.md`
- `.agents/skills/plain-language/SKILL.md`
- `.agents/skills/record-decision/SKILL.md`
- `.agents/skills/review-standard/SKILL.md`
- `.agents/skills/shape-intent/SKILL.md`
- `.agents/skills/trim-prose/SKILL.md`
- `.agents/skills/write-docs/SKILL.md`
- `AGENTS.md`
- `CLAUDE.md`
- `dev/README.i18n.yaml`
- `dev/README.md`
- `dev/README.zh.md`
- `dev/capabilities/TEMPLATE.i18n.yaml`
- `dev/capabilities/TEMPLATE.md`
- `dev/capabilities/TEMPLATE.zh.md`
- `dev/contracts/TEMPLATE.i18n.yaml`
- `dev/contracts/TEMPLATE.md`
- `dev/contracts/TEMPLATE.zh.md`
- `dev/contracts/kinds.i18n.yaml`
- `dev/contracts/kinds.md`
- `dev/contracts/kinds.zh.md`
- `dev/review/TEMPLATE.i18n.yaml`
- `dev/review/TEMPLATE.md`
- `dev/review/TEMPLATE.zh.md`
- `docs/README.i18n.yaml`
- `docs/README.md`
- `docs/README.zh.md`
- `docs/adopting.i18n.yaml`
- `docs/adopting.md`
- `docs/adopting.zh.md`
- `docs/check-catalog.md`
- `docs/documentation.i18n.yaml`
- `docs/documentation.md`
- `docs/documentation.zh.md`
- `docs/evolution.i18n.yaml`
- `docs/evolution.md`
- `docs/evolution.zh.md`
- `docs/extending.i18n.yaml`
- `docs/extending.md`
- `docs/extending.zh.md`
- `docs/glossary.i18n.yaml`
- `docs/glossary.md`
- `docs/glossary.zh.md`
- `docs/i18n.i18n.yaml`
- `docs/i18n.md`
- `docs/i18n.zh.md`
- `docs/kit.i18n.yaml`
- `docs/kit.md`
- `docs/kit.zh.md`
- `docs/plain-language.i18n.yaml`
- `docs/plain-language.md`
- `docs/plain-language.zh.md`
- `docs/testing.i18n.yaml`
- `docs/testing.md`
- `docs/testing.zh.md`
- `tests/AGENTS.md`
- `tests/README.i18n.yaml`
- `tests/README.md`
- `tests/README.zh.md`
- `tools/AGENTS.md`
- `tools/README.i18n.yaml`
- `tools/README.md`
- `tools/README.zh.md`
- `tools/pair-docs.py`

- **PASS (exit 0)** — `python3 tools/pair-docs.py --check`

  ```
  pair-docs: 21 pair(s) in scope, all complete and in step.
  ```

- **manual** — read one changed pair end to end: the counterpart must say what the authored side says, not merely something similar

### agent-inputs

Anything an agent reads is an input to a model request, so it has to be enumerable and its edits reviewed as behaviour. A skill whose description stops naming when it loads has been silently deleted from the agent's reach.

- `.agents/notes/AGENTS.md`
- `.agents/notes/README.md`
- `.agents/notes/implemented/TEMPLATE.md`
- `.agents/notes/proposed/TEMPLATE.md`
- `.agents/skills/apply-distrust/SKILL.md`
- `.agents/skills/cut-release/SKILL.md`
- `.agents/skills/define-acceptance/SKILL.md`
- `.agents/skills/engineer-tests/SKILL.md`
- `.agents/skills/land-changes/SKILL.md`
- `.agents/skills/measure-perf/SKILL.md`
- `.agents/skills/migrate-and-retire/SKILL.md`
- `.agents/skills/pick-evidence/SKILL.md`
- `.agents/skills/plain-language/SKILL.md`
- `.agents/skills/record-decision/SKILL.md`
- `.agents/skills/review-standard/SKILL.md`
- `.agents/skills/shape-intent/SKILL.md`
- `.agents/skills/trim-prose/SKILL.md`
- `.agents/skills/write-docs/SKILL.md`
- `AGENTS.md`
- `CLAUDE.md`
- `dev/README.md`
- `dev/capabilities/TEMPLATE.md`
- `dev/contracts/TEMPLATE.md`
- `dev/review/TEMPLATE.md`
- `docs/README.md`
- `docs/README.zh.md`
- `docs/adopting.md`
- `docs/adopting.zh.md`
- `docs/check-catalog.md`
- `docs/documentation.md`
- `docs/documentation.zh.md`
- `docs/evolution.md`
- `docs/evolution.zh.md`
- `docs/extending.md`
- `docs/extending.zh.md`
- `docs/glossary.md`
- `docs/glossary.zh.md`
- `docs/i18n.md`
- `docs/i18n.zh.md`
- `docs/kit.md`
- `docs/kit.zh.md`
- `docs/plain-language.md`
- `docs/plain-language.zh.md`
- `docs/testing.md`
- `docs/testing.zh.md`
- `tests/AGENTS.md`
- `tests/README.md`
- `tools/AGENTS.md`
- `tools/tiers.json`

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

### workflow

A guard is only real if a violation makes it red; a skill is only real if its description names when it fires. `.gitignore` is here because it is repository configuration like the rest of this list, and without a pattern that matches it a change to it is an unowned path — the one file every project has and no surface claimed.

- `.agents/skills/README.i18n.yaml`
- `.agents/skills/README.md`
- `.agents/skills/README.zh.md`
- `.agents/skills/apply-distrust/SKILL.md`
- `.agents/skills/cut-release/SKILL.md`
- `.agents/skills/define-acceptance/SKILL.md`
- `.agents/skills/engineer-tests/SKILL.md`
- `.agents/skills/land-changes/SKILL.md`
- `.agents/skills/measure-perf/SKILL.md`
- `.agents/skills/migrate-and-retire/SKILL.md`
- `.agents/skills/pick-evidence/SKILL.md`
- `.agents/skills/plain-language/SKILL.md`
- `.agents/skills/record-decision/SKILL.md`
- `.agents/skills/review-standard/SKILL.md`
- `.agents/skills/shape-intent/SKILL.md`
- `.agents/skills/trim-prose/SKILL.md`
- `.agents/skills/write-docs/SKILL.md`
- `tools/AGENTS.md`
- `tools/README.i18n.yaml`
- `tools/README.md`
- `tools/README.zh.md`
- `tools/change-scope.py`
- `tools/check-invariants.py`
- `tools/gen-docs.py`
- `tools/kitcheck/__init__.py`
- `tools/kitcheck/capabilities.py`
- `tools/kitcheck/cli.py`
- `tools/kitcheck/core.py`
- `tools/kitcheck/criteria.py`
- `tools/kitcheck/evidence.py`
- `tools/kitcheck/links.py`
- `tools/kitcheck/mirrors.py`
- `tools/kitcheck/policies.py`
- `tools/kitcheck/publication.py`
- `tools/kitcheck/records.py`
- `tools/kitcheck/registry.py`
- `tools/kitcheck/seals.py`
- `tools/kitcheck/skills.py`
- `tools/kitcheck/tiers.py`
- `tools/pair-docs.py`
- `tools/run-evidence.py`
- `tools/tiers.json`
- `tools/workflow.json`

- **PASS (exit 0)** — `python3 tools/check-invariants.py --self-test`

  ```
  PASS budget-words: a file over its word ceiling was rejected (NOTE.md: 40 words, budget is 10)
  PASS publish-stale-public: a public entry with no file behind it was rejected (docs/publish.json: public pattern 'docs/gone.md' matches no file — a published promise with nothing behind it is a broken link)
  PASS publish-public-glob: a public entry that publishes a whole directory was rejected (docs/publish.json public[0] 'docs/*.md': publication is a per-file promise — name the file, do not publish a directory)
  PASS publish-ambiguous: one file matched by both classes was rejected (docs/a.md: matches 2 declared patterns ('docs/a.md' (public), 'docs/*.md' (internal)) — a file has exactly one class)
  PASS publish-no-reason: an internal entry with no reason was rejected (docs/publish.json internal[0] 'docs/b.md': missing 'reason' — say why this file stays in the repository)
  PASS seal-unsealed-file: an archive file nobody sealed was rejected (archive/b.md: is in the archive but carries no seal — register it in archive/manifest.json; an archive is append-only, so nothing lands here unsealed)
  PASS seal-missing-file: a seal whose file no longer exists was rejected (archive/manifest.json sealed[0]: sealed file not found: archive/gone.md)
  PASS seal-drifted-file: a sealed file edited after sealing was rejected (archive/manifest.json sealed[0]: archive/a.md changed after it was sealed — recorded 8a6656423503…, found bc9f4a85d8f8…; an archived record is frozen: restore its bytes, or retire it and write a new record)
  PASS seal-header-mismatch: a seal whose date disagrees with the record it seals was rejected (archive/manifest.json sealed[0]: archive/a.md carries no 'Archived: 2026-02-02' line — the seal and the record it seals must name the same date)
  PASS seal-outside-patterns: a seal outside the scanned corpus was rejected (archive/manifest.json sealed[0]: .agents/notes/x.md matches none of ['archive/*'], so its seal is never verified)
  PASS seal-empty-archive: an archive with nothing sealed is accepted, and the unsealed-file probe keeps the guard live
  check-invariants: self-test PASSED — every check rejects an invalid fixture and accepts a valid one, a check with no subject is rejected, and every registration direction is covered
  ```

- **manual** — read each skill's description: it must name when the skill loads, not what it contains

### capabilities

A capability nobody classifies is invisible, and a classification whose subject is gone is stale; both are drift.

- `dev/capabilities/TEMPLATE.i18n.yaml`
- `dev/capabilities/TEMPLATE.md`
- `dev/capabilities/TEMPLATE.zh.md`
- `dev/capabilities/registry.json`

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

- **manual** — confirm each capability's definition, providers, and consumers are still real, and that a new capability arrived with its marker, registry entry, and consumer

### contracts

A pasted interface is a second fact until a check binds it to its source; an unregistered paste is the drift you cannot see.

- `dev/contracts/TEMPLATE.i18n.yaml`
- `dev/contracts/TEMPLATE.md`
- `dev/contracts/TEMPLATE.zh.md`
- `dev/contracts/kinds.i18n.yaml`
- `dev/contracts/kinds.md`
- `dev/contracts/kinds.zh.md`
- `dev/contracts/mirrors.json`

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

- **manual** — confirm every mirror's source region still exists and the paste moved in the same change as the source

### reviews

A review that only restates the author's summary confirms nothing.

- `dev/review/TEMPLATE.md`
- `dev/review/TEMPLATE.zh.md`

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

- **manual** — the reviewer must not be the author; confirm that Evidence is an external observation

### docs

Documentation has one home per fact and a declared kind; a page whose kind or owner changed is a behaviour change for every reader and every agent that loads it. A claim you did not re-run is not a fact.

- `docs/README.i18n.yaml`
- `docs/README.md`
- `docs/README.zh.md`
- `docs/adopting.i18n.yaml`
- `docs/adopting.md`
- `docs/adopting.zh.md`
- `docs/check-catalog.md`
- `docs/documentation.i18n.yaml`
- `docs/documentation.md`
- `docs/documentation.zh.md`
- `docs/evolution.i18n.yaml`
- `docs/evolution.md`
- `docs/evolution.zh.md`
- `docs/extending.i18n.yaml`
- `docs/extending.md`
- `docs/extending.zh.md`
- `docs/glossary.i18n.yaml`
- `docs/glossary.md`
- `docs/glossary.zh.md`
- `docs/i18n.i18n.yaml`
- `docs/i18n.md`
- `docs/i18n.zh.md`
- `docs/kit.i18n.yaml`
- `docs/kit.md`
- `docs/kit.zh.md`
- `docs/plain-language.i18n.yaml`
- `docs/plain-language.md`
- `docs/plain-language.zh.md`
- `docs/publish.json`
- `docs/testing.i18n.yaml`
- `docs/testing.md`
- `docs/testing.zh.md`

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

- **manual** — read the changed page as its reader: does it still say what a reader can do, and does its kind still match its content

### generated-docs

A generated page is a projection of its source. Hand-patching it creates a second fact that the next regeneration deletes, and a generator edit without a regeneration leaves the committed page stale.

- `docs/check-catalog.md`
- `tools/gen-docs.py`

- **PASS (exit 0)** — `python3 tools/gen-docs.py --check`

  ```
  gen-docs: docs/check-catalog.md is up to date (88 lines).
  ```

- **manual** — confirm the page was produced by the generator, never patched by hand

### evolution

Evolving and retiring are the two stages where an old fact comes back as a current one: a guide nobody wrote leaves the reader to discover the break, and an archive that can still be edited is not an archive.

- `docs/evolution.md`

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

- **manual** — confirm every retired record entered .agents/notes/archived/ and its seal moved in the same change, and that nobody rewrote a consumed generation instead of adding a successor
- **manual** — read the policy as its reader: does each break still have exactly one guide, and does each guide still say how to confirm the migration

### tests

A test is evidence only when its command and its failure mode are named. A test nobody runs, or one that cannot fail, proves nothing.

- `tests/AGENTS.md`
- `tests/README.i18n.yaml`
- `tests/README.md`
- `tests/README.zh.md`
- `tests/__init__.py`
- `tests/harness.py`
- `tests/test_corpus.py`
- `tests/test_evidence.py`
- `tests/test_pairing.py`
- `tests/test_policies.py`
- `tests/test_records.py`
- `tests/test_tiers.py`

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
  Ran 83 tests in 0.468s
  OK (skipped=2)
  ```

- **manual** — run the owning test for each changed test file, and watch it fail for the regression it pins
- **manual** — read docs/testing.md: judge whether the tier, the determinism rules, and the negative control still hold

### source

Behaviour changes need the smallest check that would fail for the regression. This repository has two runtimes: the Python backend under backend/ (syntax gate here) and the C# plugin that embeds Web/inject.js and Web/style.css (built by ./build.sh). There is no automated test suite yet, so a change to either runtime also owes the manual smoke path in AGENTS.md.

- `tests/__init__.py`
- `tests/harness.py`
- `tests/test_corpus.py`
- `tests/test_evidence.py`
- `tests/test_pairing.py`
- `tests/test_policies.py`
- `tests/test_records.py`
- `tests/test_tiers.py`
- `tools/change-scope.py`
- `tools/check-invariants.py`
- `tools/gen-docs.py`
- `tools/kitcheck/__init__.py`
- `tools/kitcheck/capabilities.py`
- `tools/kitcheck/cli.py`
- `tools/kitcheck/core.py`
- `tools/kitcheck/criteria.py`
- `tools/kitcheck/evidence.py`
- `tools/kitcheck/links.py`
- `tools/kitcheck/mirrors.py`
- `tools/kitcheck/policies.py`
- `tools/kitcheck/publication.py`
- `tools/kitcheck/records.py`
- `tools/kitcheck/registry.py`
- `tools/kitcheck/seals.py`
- `tools/kitcheck/skills.py`
- `tools/kitcheck/tiers.py`
- `tools/pair-docs.py`
- `tools/run-evidence.py`

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
