# Adopting into a repository that already exists

English | [中文](adopting.zh.md)

The "30-second start" in [kit.md](kit.md) assumes an empty repository. This page is the same install where there is already code, already documentation, and already consumers — the case where the order of operations decides whether the kit survives its first week.

## Four collisions

**Your repository already has conventions.** The kit's files are defaults, and a default loses to a convention that is already here. Two files are the project's, not the kit's: the root `AGENTS.md` names your own entry map under `## Read first` and your own commands under `## Commands`, and the kit's `<docs/entry.md>` and `<check>` are placeholders that read as satisfied while guarding nothing.

**Your repository already has documentation.** `publish-manifest` requires every `*.md` classified exactly once in [publish.json](publish.json), and the `i18n` surface requires every `*.md` outside the exclusion table to exist as a pair. On a repository of hundreds of pages that classification is the real work of adopting, and it is mechanical. Two honest paths: classify them all, or narrow the check's `patterns` in [workflow.json](../tools/workflow.json) to the tree you maintain from now on. The second is configuration, not an exemption — an adopted project runs the same scripts with a different configuration ([rule](extending.md#syncing-a-change-into-a-copy-of-the-kit)). A single-language repository deletes the Chinese sides and comments out the `i18n` surface check in the same change ([reason](kit.md)).

**Your repository already has consumers.** Moving a published entry point, a configuration key, a stored format, or a wire API changes something already consumed, and the kit's answer is [evolution.md](evolution.md): a versioned successor, an upgrade guide written in the change that breaks it, and never a moved or deleted generation. That guide's home, `dev/upgrade-guide/`, belongs to the **delivery tier** — so if a consumed surface can break, install that station, because on the minimum tier nothing records the promise.

**Your repository already has CI.** The kit ships no CI on purpose ([why](kit.md)). Your pipeline keeps its matrix; wire these commands into it where they belong, and let [pick-evidence](../.agents/skills/pick-evidence/SKILL.md) decide which of them a given change owes.

## The order that works

1. Copy the **minimum tier** and delete the kit's own records ([commands](kit.md)).
2. Declare every station you did not install `none` in [tools/tiers.json](../tools/tiers.json) **and remove its directory** — the switch states intent, the tree states fact, and either one disagreeing is red ([the ladder](../dev/README.md#adoption-tiers)).
3. Narrow [workflow.json](../tools/workflow.json): surface patterns, each evidence command, check patterns.
4. Classify the existing Markdown in [publish.json](publish.json), and settle the language question in the same change.
5. Fill the two placeholders in the root `AGENTS.md`.
6. Delete the skills you will not use, leave the directory non-empty, and point your host's loader at it.
7. `python3 tools/run-evidence.py --check` — a configuration that cannot resolve is red before anything runs.
8. Run every command in [AGENTS.md](../AGENTS.md#commands) and confirm it really runs here.

## What not to do

- **Do not copy kit files over files of the same name.** A wholesale copy replaces your `AGENTS.md` and `workflow.json` with the kit's template, and the loss is quiet: a placeholder reads as manual evidence, deleted policy reads as "this project declares nothing", and the gates stay green ([rule](extending.md#syncing-a-change-into-a-copy-of-the-kit)).
- **Do not chase all-green on day one.** A station you did not install reds for matching no file, deliberately; the exit is declaring it `none`, not manufacturing files to satisfy it.
- **Do not import the kit's decision history.** Its records are one project's past, not your policy.
- **Do not install all eleven stations "to be safe."** The checks cost maintenance, and that cost is what the tiers exist to bound ([tradeoff](../tools/README.md)).

## The refactor itself

With the kit in place, the work you came for is a change like any other: intent and acceptance at station 1, evidence at station 6, review at station 7 ([the map](../dev/README.md)). Where it breaks something already consumed, [migrate-and-retire](../.agents/skills/migrate-and-retire/SKILL.md) picks it up.
