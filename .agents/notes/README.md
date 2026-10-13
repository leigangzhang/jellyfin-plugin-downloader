# Decision records

English | [中文](README.zh.md)

One kind of design doc lives here. A **decision record** keeps a decision or a proposal that shapes this kit — the *why* and what we gave up, the parts code and docs cannot carry. This file defines where records live, when to write one, and the in-file format.

## Layout and naming

Every record has two axes, both encoded in its **path** — `.agents/notes/{lifecycle}/{class}/yyyy-mm-dd-topic-title.md`:

- **Lifecycle** (the top-level folder) is the record's status, and a record moves between folders as that status changes:
  - **`proposed/`** — designed but not built, or only partly; gate `decision-proposed`.
  - **`implemented/`** — the decision shipped, and **kept current with what shipped**: a later path, name, or default change updates the record in the same change — facts only, never the decision; gates `decision-implemented`, `no-proposal-era-headings`.
  - **`rejected/`** — considered and declined; keep it only while its rationale prevents a tempting, meaningful mistake, otherwise delete the triplet; gate `decision-rejected`.
- **Class** (the nested folder) is the *kind* of decision, from the closed set in the next section; gate `note-class`.

The date in the filename is when the topic was **first proposed**, per git history. Records cross-reference each other with relative Markdown links, never bare prose or numbers: a link survives a move, and `pair-docs.py --check` resolves its fragment.

The active tree is the inventory: browse its lifecycle and class folders, or start from the index in [docs/README.md](../../docs/README.md). Do not add a centralized index page for it — a second list is a second fact. Records with historical decision value but little future guidance move to the frozen `archived/` tree described below.

Templates live one level up, at `.agents/notes/<lifecycle>/TEMPLATE.md`: the skeleton is keyed by the lifecycle, not the class, and each one keeps its checks from matching no file — an empty corpus is void, not passing.

## Classification

Each record belongs to one path-encoded class from the closed set declared in the `note-class` check in [workflow.json](../../tools/workflow.json), which rejects a folder outside the set, an unknown lifecycle, a missing `Class:` line, and a `Class:` line that disagrees with its folder. Adding a class means updating that list and this table together.

| Class | What it covers |
|---|---|
| `feature` | A new user- or model-facing capability. |
| `bug-fix` | Corrects a defect, or closes a gap an incident record surfaced. |
| `simplification` | Removes behaviour or surface area without adding a capability. |
| `architecture` | A structural decision about what this kit ships — how its parts relate, what its vocabulary is. |
| `process` | Tooling, policy, or workflow around the artefact: gates, records, publication, documentation rules. |
| `testing` | Test infrastructure and strategy. |

The `architecture` / `process` line: architecture is about the artefact itself; process is the surrounding machinery and the rules that govern it. `refactor` is deliberately absent — its only discriminator, "does observable behaviour change?", is already `simplification`'s.

## Modules

A record's topic title opens with the **subject** the decision belongs to: the domain it is about, never the folder it is filed in or where the code it describes sits. A record is a sub-record of one subject, and moving the code does not move it between subjects. Gate `note-class` rejects a title whose first token is not in the table below.

The class set above is closed and adopted. This one is open and this project's own: a subject is named once and reused, it grows when a decision belongs to a domain nothing here names yet, and a new project starts with its first entry.

| Module | What it covers |
|---|---|
| `adoption` | Copying the kit in: what arrives, and what must not. |
| `docs` | The standing documents — kinds, placement, budgets, publication. |
| `gates` | The check engine, the checks, the evidence they read. |
| `i18n` | Paired documents: what pairs, and how the sides stay in step. |
| `intent` | A goal becoming proposals — how many, of which class, judged by what. |
| `lifecycle` | The eleven stations, the tier ladder, the version states. |
| `records` | The records — rules, layout, taxonomy, titles, retirement. |
| `testing` | Test infrastructure, the tiers, the flake policy. |
| `trust` | The stance an agent takes toward its own signals. |

**How the list is kept.** Look the subject up before you name a file, by the words a reader would search for — a near match is a match. Reuse it whenever the decision lives in a domain an entry already names; a list that grows with every record is a list nobody reads. Add a row only when nothing here is where the decision belongs, in the same change as the record that needs it, and name a **domain, not a topic** — `sandbox`, not `new-sandbox-flag`. Never rename an entry a record already uses, because records are addressed by their paths: a subject that splits gains a second row, and the first leaves when the last record that used it is gone. In reverse, an entry no record reaches a file through is a name the next reader will reuse by mistake.

## Archiving and deletion

Delete an implemented record that only describes a mechanical or local change — its English, Chinese, and sidecar files together, with every inbound link repaired. A small bug fix, a new capability, or a substantive decision does not qualify merely because its implementation is small.

Seal a record into `.agents/notes/archived/{class}/yyyy-mm-dd-topic-title.md` when the decision is complete, its rationale is unlikely to guide future work, and it still owns history. `implemented` is deliberately absent from that path, because only implemented records can enter it. Keep a record active while its alternatives, an ownership boundary, a negative guarantee, durable or wire semantics, a security rule, or a reintroduction condition still guides anyone. Never archive a proposal: reject it. A rejected record lives only while it prevents a plausible mistake, and is otherwise deleted with its triplet. The delete-versus-merge-versus-seal call is the calibrated one in [migrate-and-retire](../skills/migrate-and-retire/SKILL.md), never word count, age, or a target quota; the selector itself lives in [evolution.md](../../docs/evolution.md).

Archiving moves the complete triplet, keeps `Status: implemented`, and inserts the same `Archived: <yyyy-mm-dd>` line immediately below the status in both language files, re-records the sidecar, and repairs or deletes inbound links. Those are the only permitted content changes during archival; existing title punctuation, blank-line layout, and language-switcher wording do not block it and are preserved with the body. Gate `archive-seal` binds a sealed file's text to its digest, archive date, and reason in [manifest.json](archived/manifest.json), so an unregistered drop, a missing file, an edited byte, and a disagreeing date are all red. Once sealed, a record is frozen: never edited, reformatted, translated, updated, moved, or deleted, and never treated as authority for current behaviour. Documentation gates skip archived sources, including their outbound links, while active prose may still link into an archived record when it intentionally cites history.
## When to write one

Add or update a record in the same delivery that lands the work — the pull request, or the commit where there is no pull request — only for lasting decision rationale that code, tests, and existing documentation do not explain. A proposal for substantial future work starts in `proposed/`; a decision already made starts in `implemented/`. Pick the class folder that matches the decision ([meanings](#classification)).

Updating the record that already owns the decision satisfies the rule; do not create a duplicate. Mechanical or local edits, including local presentation and interaction changes, are exempt. A record is never edited into a *different decision*: supersede it with a new one, and keep both records cross-linked unless the old one is later fully consolidated under the rule below. Editing an `implemented/` record to track where its existing decision lives is required, not forbidden.

An implemented record that is fully superseded may be consolidated into the current owning record and deleted. Before deletion, the owner must preserve every unique rationale, alternative, consequence, required verification, and named coverage gap; repair every inbound link; and delete the Chinese counterpart and the consistency record in the same change. Partial supersession does not qualify: keep both records cross-linked and update every fact that remains current. Consolidation must not rewrite the old record into its opposite, and must not rely on git history as the only copy of its rationale.

A capability record may be consolidated into the record that removes it only when the capability is absent from production code, configuration, schemas, durable or wire formats, migration, and compatibility behavior; no current document presents it as available; and no test exercises it as supported behavior. Removal rationale and tests that verify absence may remain. The removing record preserves the original motivation, why it no longer justified keeping the capability, the alternatives to full removal, the capability given up, the conditions for reintroduction, and the verification of complete absence. Obsolete implementation inventories and tests that only verified the deleted behavior are not current verification evidence. Removing one transport, default, implementation, or presentation is partial supersession, as is any surviving durable data or compatibility handling.

A record is written by hand, not by the turn that produced the work: `/record-decision` freezes the decisions a session settled ([skill](../skills/record-decision/SKILL.md)).

## The file format

Every active record follows one in-file format, enforced by `decision-proposed`, `decision-implemented`, `no-proposal-era-headings`, `criteria-traced`, and `note-class`. The literal skeletons are the templates: one copy of the format, not two. Archived records keep the format they had when sealed, plus the archive-date line above.

### The header block

The first lines of every record are these, in this order:

| Line | Value |
|---|---|
| `# Proposal: <title>` or `# Decision: <title>` | The lifecycle's own prefix: a proposal keeps `Proposal`, a moved record carries `Decision` |
| `Status: <status>` | `proposed`, `implemented`, or `rejected — <why, in one line>`; it must agree with the folder |
| `Date: <yyyy-mm-dd>` | The first-proposed date, and the same date the filename carries |
| `Class: <class>` | The path class, verbatim; `note-class` cross-checks it against the folder |

The status carries no dates and no parentheticals beyond the rejection reason: the filename holds the date, and the record holds everything else. A record accepted in amended form states the amendment in its body, not in the status. The rejection reason is the one status with content, because a rejected record's verdict is the fact readers come for.

### The body skeleton

Every record opens with `## Problem` — the motivation, written to stand without the solution. Recurring sections use these names and nothing else; a genuinely bespoke section — a gate's contract, a table shape, a package topology — sits between the required ones.

- `proposed/`: `## Problem` · `## Proposal` · `## Alternatives considered` · `## Acceptance criteria` · `## Risks`.
- `implemented/`: `## Problem` · `## Decision` · `## Alternatives considered` · `## Consequences` · `## Testing`.
- `rejected/`: the proposal as frozen; the verdict lives on the `Status:` line.

`## Proposal` may speak in the future tense — plans, migration steps, and open questions belong there while the work is unbuilt. `## Acceptance criteria` says what observable state means done. `## Risks` covers both what could go wrong and what the change knowingly gives up. `## Decision` states shipped reality in the present tense. `## Testing` keeps each criterion's id alive, and `## Consequences` records what the trade-off cost as well as what it bought. A `## Deferred` or `## Related` section is fine where it states present-tense fact. A rejected record keeps whatever proposal-time sections it had, including `## Acceptance criteria` or `## Plan`; only the header block, the `## Problem` opener, a `## Proposal` section, and the mandatory alternatives section apply to it.

### Alternatives considered — mandatory

Every record carries `## Alternatives considered`, and it may not be empty: each genuine alternative with why it lost, one bold-led paragraph each, or a `### Why not <X>?` subsection for a contested one. A decision recorded without what it beat invites re-litigation. Alternatives are recorded, never invented. The requirement is gated in both languages, so a translated record cannot quietly drop it.

### Moving between lifecycles

Moving a record means updating the `Status:` line and re-satisfying the target folder's skeleton in the same change — the gate fails the move otherwise. `proposed/` → `implemented/` rewrites `## Proposal` into a present-tense `## Decision`, folds `## Acceptance criteria` and `## Risks` into `## Consequences` (or a present-tense `## Testing` for what now pins the behaviour), and keeps every criterion id — the id tells a reader which check proves which criterion. `proposed/` → `rejected/` adds the reason to the status line and freezes the body. Gate `no-proposal-era-headings` rejects a moved record still carrying `## Proposal`, `## Plan`, `## Migration plan`, or `## Acceptance criteria`.

### Chinese counterparts

A `.zh.md` counterpart mirrors its English sibling's structure section for section, with a `.i18n.yaml` record beside the pair ([i18n.md](../../docs/i18n.md)). The machine-checked header tokens — `# Proposal: `, `# Decision: `, `Status:`, `Date:`, `Class:` — stay in English verbatim; only headings and prose are translated. The format gate skips `.zh.md` files; the pairing gate checks their consistency.

## Where to look

- How to write one: the skeletons in [.agents/notes/proposed/TEMPLATE.md](proposed/TEMPLATE.md) and [.agents/notes/implemented/TEMPLATE.md](implemented/TEMPLATE.md).
- How a goal becomes one or more records: the trigger in [.agents/skills/shape-intent/SKILL.md](../skills/shape-intent/SKILL.md).
- Which record a change needs: the selector in [evolution.md](../../docs/evolution.md).
- What each section must contain: the document kinds in [documentation.md](../../docs/documentation.md).
- The rules above, and the skeletons in `proposed/` and `implemented/`: this kit ships no records of its own, and a project's records collect here newest first by file name.
