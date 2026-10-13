# documentation.md

English | [中文](documentation.zh.md)

The documentation standard for this repository. The rules hold for any repository; the commands named here are this kit's own, so every one of them really runs. Station entry and exit live in [dev/README.md](../dev/README.md#--documentation-cross-cutting); this file owns the rules themselves.

A **skill** is the one kind whose reader is a model: its `description` is a load condition (`Use when …`), not a summary; its body carries the decision logic; and its last section names the gate to run. No validation logic lives in a skill. The host owns the loader, and `skill-trigger` proves the description is still a trigger, never that it was loaded.

## Document kinds

Every document gets one kind first, and the kind fixes its skeleton, its budget, and its reader. The kind follows from **mechanical facts** — which question it answers, and who reads it — never from the directory name.

| kind | It answers | Not its job |
|---|---|---|
| guide ([kit.md](kit.md), [dev/README.md](../dev/README.md), this file, [testing.md](testing.md), [i18n.md](i18n.md), [evolution.md](evolution.md)) | One learning path, or one current rule | Restating what the homes it links to already say |
| index ([docs/README.md](README.md)) | Where to find what | Any rule or fact of its own |
| generated ([docs/check-catalog.md](check-catalog.md)) | Facts exported exhaustively from one source | Any hand-written content |
| contract ([dev/contracts/TEMPLATE.md](../dev/contracts/TEMPLATE.md), [dev/capabilities/TEMPLATE.md](../dev/capabilities/TEMPLATE.md)) | An interface someone must honour toward its callers | Behaviour narration and rationale (→ decision records) |
| record ([.agents/notes/](../.agents/notes/implemented/TEMPLATE.md), [dev/review/](../dev/review/TEMPLATE.md), [dev/release/](../dev/release/TEMPLATE.md), [dev/postmortem/](../dev/postmortem/TEMPLATE.md)) | One decision, review, release, or incident | Current-state rules (→ their own rule homes) |
| skill ([.agents/skills/](../.agents/skills/README.md)) | When to do what | Decision logic (→ gates) and contracts (→ source or `dev/contracts/`) |
| template (`*/TEMPLATE.md`) | What a record should look like | Real content |
| reference ([glossary.md](glossary.md), [plain-language.md](plain-language.md)) | A current fact looked up by name or by term | Teaching paths, rationale, generated catalogs |

To add a kind, supply all three at once: its skeleton (`required-sections`), its home (a directory or `patterns`), and a check that maps documents to it. With one missing, no reader can say whether a document of that kind is acceptable.

**Names.** Uppercase is for names a tool or a reader looks up by name: `README.md`, `AGENTS.md`, `CLAUDE.md`, `SKILL.md`, `TEMPLATE.md`. Every other file is lowercase kebab-case, policy documents included. A name is a label, never a kind signal: the kind follows from the mechanical facts, so a change of kind renames the file.

## One home per fact

A fact lives in one home; everywhere else links there:

| One fact | Its home | Not its home |
|---|---|---|
| Standing rules (in context for every task) | [AGENTS.md](../AGENTS.md) | Stories, examples, situational flows |
| Station entry and exit | [dev/README.md](../dev/README.md) | Item-by-item checklists (→ `tools/workflow.json`) |
| A station's records | `dev/<station>/` (see [dev/README.md](../dev/README.md)) | The station's rules (→ `docs/`) or a decision's rationale (→ [.agents/notes/](../.agents/notes/implemented/TEMPLATE.md)) |
| Test strategy | [testing.md](testing.md) | Concrete test commands (→ the changed surface's evidence) |
| Evolution and retirement rules | [evolution.md](evolution.md) | One break's concrete migration steps (→ `dev/upgrade-guide/`) |
| Documentation rules | this file | Product contracts (→ README or source) |
| Why a choice was made, and what it gave up | [.agents/notes/](../.agents/notes/implemented/TEMPLATE.md) | Current-state rules |
| Language, pairing, and translation of terms | [i18n.md](i18n.md) | What a word means and which to use (→ [glossary.md](glossary.md)) |
| What a word means and which spelling to use | [glossary.md](glossary.md) | Translation pairs (→ [i18n.md](i18n.md#terminology)) |
| Interfaces, types, config keys | The declaration in source | A copy in a document, unless it is registered as a mirror |
| The check engine's declared kinds | The registered mirror in [dev/contracts/kinds.md](../dev/contracts/kinds.md), which is the seam's definition | The provider's file as the readable definition, or a second copy |
| A capability's roles | `dev/capabilities/registry.json`, bound to the `# capability:` markers | Roles kept only in the marker, or a second registry |
| Which tier a project runs | `tools/tiers.json`, the only tier declaration, read by `tier-manifest` | A directory that happens to exist, or a check commented out by hand |
| What may be published | [docs/publish.json](publish.json) | Any second allow-list |
| What a gate is actually checking | `tools/workflow.json` | A hand-copied checklist (→ the generated [docs/check-catalog.md](check-catalog.md)) |

**Restatement is drift**: when a sentence appears twice, delete one copy and link the other. A restatement a machine can catch is pinned as a `forbidden-regex` (see [The slop checklist](#the-slop-checklist)).

## Tutorial or reference

Every document is one or the other, decided by **purpose**, not by path:

- **tutorial**: an ordered path to a result, introducing only the concepts each step needs right then;
- **reference**: a lookup scope describing current behaviour, with no teaching order.

Substantial in both → split it into two documents. Only a small part is the other form → label that section in place. Write the reader's starting point, the observable result, and the most likely failure and recovery path before the details. Order a tutorial by prerequisite, not by implementation order.

## Generated, mirrored, or linked

A fact enters documentation in exactly three ways, and the default is the third:

1. **link**: the fact lives in source, configuration, or a generator, and the document says what it is and where to look.
2. **mirror**: when it must be shown verbatim, a fenced block in the document plus a `dev/contracts/mirrors.json` entry, compared byte for byte by `source-mirror` against the source region between its `begin`/`end` markers. Registration and paste update in the same change.
3. **generate**: a whole page exported by a generator from one source. A generated page is read-only: change the generator, re-run the generation command, and never patch the page. Ask "is a link or a mirror enough?" before reaching for this one.

A generated page's authority comes from **the generator plus the freshness gate**, never from the text itself. So every generated page declares a `--check` command on its surface in `tools/workflow.json`, which `run-evidence` really runs and records in one of three states. The living examples here are `docs/check-catalog.md` with `tools/gen-docs.py --check`, and every bilingual pair with `tools/pair-docs.py --check`.

(DeepSeek Harness implements the same idea: `gen-doc-graphs.ts --check` compares a generated set byte for byte. Its other generation discipline is "one source, several projections" — one generator emits documentation and runtime code together, so the documentation cannot rot. A bilingual pair is a fourth form with its own home in [i18n.md](i18n.md);.)

## Budgets

A standing document is a budget, not a warehouse. Over budget, in this order:

1. **relocate**: the content belongs in another home → move it there and leave a one-line link;
2. **condense**: it does belong here, but it can be shorter;
3. **raise the number**: only when the words genuinely need the space, and say why in the commit.

The `budget` check counts lines (`maxLines`) or words (`maxWords`, whitespace-separated, the same unit as `wc -w`). A word count badly undercounts prose with no space between words, so a Chinese standing document carries a line ceiling and its English side a word ceiling — one entry per side, as `docs-budget` and `docs-budget-zh` show. A ceiling is a guardrail, not a reduction target: within 5% of it, relocate or condense rather than adding words.

## Publication

**A file existing is not a file shipping.** [docs/publish.json](publish.json) is the only publication switch, and it classifies every file in the `publish-manifest` corpus as `public` or `internal`:

- `public` **names files one by one** and accepts no glob: publication is a promise about a file, not about a directory, and a new page must be added explicitly;
- `internal` may be declared by home (a directory) and must state why the file stays in the repository; being empty for now is fine, because it is not a promise;
- every file must hit **exactly** one declaration: a file nobody matches is a **silent omission** and a file matching two is ambiguous — both are red;
- a `public` entry with no real file behind it is a broken link, and red.

The manifest is the switch of the projection, not the projection itself: consumers build from these `public` files, and the repository Markdown stays the only editable source.

## The slop checklist

What a machine can decide is pinned as a check; what it cannot is left to review. Search for each of these:

- **duplicated rules**: search the whole text for one distinctive phrase, keep one home, and turn the rest into links; what can be pinned as a `forbidden-regex`, pin.
- **history out of bounds**: "it used to be" or "this version changed it" inside a current-state document → move it into a decision record.
- **implementation-status annotations**: "implemented", "will later…". Status rots; the repository layout and the manifests are the status.
- **hand-copied catalogs**: when source or a generator is authoritative, do not copy a list of tools, events, packages, or checks into a document.
- **reasoning-trace leakage**: step-by-step implementation narration, proof of an obvious branch, test walk-throughs, rejected local alternatives. Keep the conclusion, delete the path.
- **paragraph walls**: one paragraph carrying several rules and parenthetical asides. Split it, or demote the detail to its home.
- **emphasis inflation**: bold and "critically" everywhere means nothing stands out.
- **prose restating code**: comments and documents restating control flow instead of writing down the contract, the failure modes, the timing, and the ownership.

## Known limits

- `budget` counts `maxWords` as `wc -w` does, so text without spaces between words is badly undercounted; only whitespace-separated prose suits a word ceiling. This kit's Chinese pages therefore carry a line ceiling — the live example is this pair.
- `publish-manifest` guarantees only that every file is classified once, that `public` names real files one by one, and that `internal` states a reason. Whether a site build really picks those files up is the build command on the `docs` surface, not this gate.
- **Generated-page freshness is not decided by `check-invariants`**: it runs no generator. `--check` is evidence that `run-evidence` executes, so until that runs nobody has proved the page is current.
- `source-mirror` compares **text regions, not ASTs**: it catches a paste that did not follow its source, not two declarations written differently that mean the same thing. Per-language AST comparison is your work.
- `source-mirror` mirrors only the fence info strings listed in the manifest, so a misspelled info string (`text miror`) silently makes a block an ordinary example. Keep mirror blocks under `dev/contracts/` with a fixed info string, or verify them in review.
