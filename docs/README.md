# Documentation index

English | [中文](README.zh.md)

What this kit's documents are, and what each one answers. This page only routes: the rules and the facts live in the homes it links to, and nothing is restated here. It is also the one place that lists every home and what that home owns.

| Your question | Where to go |
|---|---|
| What is this kit, and how do I install it into my repository? | [kit.md](kit.md) |
| What does each of the eleven stations produce, and which gate decides it? | [dev/README.md](../dev/README.md) |
| How does a break leave a migration path for its readers, and how does an old record retire or get sealed? | [evolution.md](evolution.md) |
| How should this document be written, where does it live, what budget does it get, and may it be published? | [documentation.md](documentation.md) |
| Are these documents bilingual, and how do the two sides stay in step? | [i18n.md](i18n.md) |
| How does this repository decide that something is "verified"? | [testing.md](testing.md) |
| How do I extend the kit itself — a surface, a check, a skill, a budget? | [extending.md](extending.md) |
| I am bringing this kit into a repository that already exists — what changes? | [adopting.md](adopting.md) |
| What are the standing rules? | [AGENTS.md](../AGENTS.md) |
| What do the five scripts do, and where do they stop? | [tools/README.md](../tools/README.md) |
| What is a gate checking right now? | [check-catalog.md](check-catalog.md) (generated page, never edit it by hand) |
| Which documents ship, and which stay in the repository? | [publish.json](publish.json) |
| Why was a decision made this way, and what did it give up? | [.agents/notes/README.md](../.agents/notes/README.md) |
| Which rules do I follow when writing documentation? | [.agents/skills/write-docs/SKILL.md](../.agents/skills/write-docs/SKILL.md) |
| What does this word mean here, and which spelling should I use? | [glossary.md](glossary.md) |
| How do I say one of these terms to someone who has never read this kit? | [plain-language.md](plain-language.md) |

## Every home, and what it owns

| Home | Kind | Tier | Gate | It owns |
|---|---|---|---|---|
| [kit.md](kit.md) | guide | every tier | — | What the kit is, the three adoption tiers, and where to go next |
| [AGENTS.md](../AGENTS.md) | standing rules | every tier | `context-budget` | The rules an agent needs in every context, and the only command list (`context-budget` stops it at 120 lines) |
| [dev/README.md](../dev/README.md) | guide | every tier | — | The lifecycle map: the eleven stations, each one's entry and exit, its gates, and its anti-patterns |
| `docs/` | policy | every tier | `docs-policy`, `i18n-policy`, `evolution-policy`, `testing-policy`, each with a budget | The standing rules of the kit: documents, vocabulary, language, evolution, testing |
| [tools/](../tools/README.md) | machinery | every tier | — (it implements every gate) | The five scripts, their only configuration (`workflow.json`), and the rules for changing them |
| [.agents/skills/](../.agents/skills/README.md) | skills | every tier | `skill-record`, `skill-trigger` | Trigger layers, loaded by their `description` rather than read in order |
| [.agents/notes/](../.agents/notes/README.md) | records | every tier | `decision-*`, `no-proposal-era-headings`, `criteria-traced`, `note-class`, `notes-readme` | Decision records: proposals, implemented decisions, and rejections, each filed under a class folder |
| [dev/contracts/](../dev/contracts/TEMPLATE.md) | contract | minimum | `contract-record`, `contract-mirror`, `contract-kinds-budget` | Interfaces, mirrored declarations, and the contract kind list |
| [dev/capabilities/](../dev/capabilities/TEMPLATE.md) | records | minimum | `capability-record`, `capability-registry` | The capability registry: seams, cores, services, and bundles |
| [dev/review/](../dev/review/TEMPLATE.md) | records | minimum | `review-record` | Review records: the findings that only reading can produce |
| [dev/release/](../dev/release/TEMPLATE.md) | records | delivery | `release-record` | Release records: revision, scope, evidence, breaking changes, rollback |
| [dev/upgrade-guide/](../dev/upgrade-guide/TEMPLATE.md) | records | delivery | `dev/upgrade-guide`, `upgrade-guide-budget` | One migration guide per broken surface, written in the change that breaks it |
| [dev/postmortem/](../dev/postmortem/TEMPLATE.md) | records | long-lived | `postmortem-record`, `postmortem-guardrail` | Incident write-ups: why the process let a bug through |
| [.agents/notes/archived/](../.agents/notes/archived/manifest.json) | archived | long-lived | `archive-seal` | Sealed records, frozen with their digest and archive date |
| [tests/](../tests/README.md) | tests | every tier | `no-time-based-test-sync` | The kit's own contract tests, run by the Unit command in [testing.md](testing.md) |
| `dev/evidence/` | generated | generated | — | What `run-evidence.py` wrote: one verdict per command, per run |


The Tier column names the copy set in [kit.md](kit.md): `every tier` ships with all three, `generated` appears on the first `run-evidence.py` run, and `this kit only` is never copied. Every home is also covered by the universal gates — `publish-manifest`, `no-secrets`, `no-conflict-markers`, `banned-spellings` — so each Gate cell lists only the home-specific ones.