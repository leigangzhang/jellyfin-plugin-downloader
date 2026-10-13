---
name: record-decision
description: Use when the user types /record-decision or asks to freeze this session's settled decisions into records.
disable-model-invocation: true
---

# Record a decision

Every round of rework has the same cause: the reason a thing is the way it is lives only in the session that produced it. A decision record is where that reason survives.

## When to use

- The user types `/record-decision`, or asks to freeze this session's decisions in words.
- A previously recorded decision is being reversed.

This skill is user-invocable only (`disable-model-invocation: true`): the agent does not decide when a record is written. Where a host has no `/name` gesture, the same request in words loads this skill.

**Exempt:** mechanical edits, renames, formatting, and local presentation tweaks. They state no constraint, so the first question above fails on its own.

## How

**Load both sources, then judge every entry on evidence.**

Read [the record rules](../../notes/README.md#when-to-write-one), then the files under `.agents/notes/.session-decisions/`. The file is the durable baseline: it survives compaction, and it is the only thing a later session can read. The live conversation is usually richer, and it may hold decisions the file never got.

Do not decide by source — decide by evidence. A sentence enters the record only when it can name a file, a command, or a commit. Where the file and the conversation disagree, the side that can point at the evidence wins and the other side is corrected or deleted, including when the conversation is the side that is wrong. An entry neither side can support is dropped, not softened.

**Know where a session keeps what it settled.** The entries live in one file per session under `.agents/notes/.session-decisions/`, named with the session id the host reports (`$DSH_SESSION_ID` where there is one) and held by `.gitignore`. Each entry is self-contained — the question, the options, the conclusion, the reason, and the evidence — so a compacted conversation can continue from the file alone. Deleting that file is part of freezing, and freezing is per record, not per commit: a change that is not final keeps its file, and later work adds its entries to the same record.

**Keep the record to what a reader can hold.** Across the host repository's 525 implemented records the `## Decision` section states a median of 4 constraints and 80% stay within 8; this kit's own records peak at 7. Past 8, compress first — merge statements that restate one constraint and delete sentences that constrain nothing. Split only when an entry answers a question about another module, another consumer, or another independently deliverable unit.

**Before the work — copy the proposal template.**

```sh
mkdir -p .agents/notes/proposed/<class>
cp .agents/notes/proposed/TEMPLATE.md .agents/notes/proposed/<class>/<yyyy-mm-dd>-<module>-<slug>.md
```

Fill all five sections. Two of them are mandatory, and they are the ones people skip:

- **`## Alternatives considered`** may not be empty. A decision recorded without what it beat invites re-litigation. Alternatives are **recorded, never invented**: if something was genuinely never on the table, say so and say why.
- **`## Acceptance criteria`** must be translatable into an assertion or a command. If you cannot picture the check, it is a wish, not a criterion.

**When it ships — move it and rewrite the skeleton.**

```sh
# .agents/notes/proposed/<file>.md  →  .agents/notes/implemented/<file>.md, then:
#   ## Proposal                      → ## Decision   (present tense)
#   ## Acceptance criteria + ## Risks → ## Consequences
```

Moving the file without rewriting it fails the check. That is deliberate: the rewrite is the work the move always owed.

**Afterwards — keep the facts current, never the decision.**

When a path, symbol, or default changes, update the record in place. When the **decision** changes, write a new record and cross-link it. Do not append "update: later we changed it to…" — that hands the next reader two contradicting facts.

**Before you name the file — look the module up in the table.**

The topic title opens with the module the decision lives in, and that module must appear in the table under `## Modules` in [.agents/notes/README.md](../../notes/README.md), or gate `note-class` rejects the record. Look it up before you name the file: reuse an entry whenever the decision lives where one already points, and add a row only when nothing there is where the decision lives. That section states the rest — how to choose between a near match and a new name, what a module name is a name *of*, and when an entry leaves the table.

**When it stops guiding anyone**, delete it, or move it to `.agents/notes/archived/` with an `Archived:` date and never edit it again.

## Verification

- `python3 tools/check-invariants.py` — `decision-proposed` and `decision-implemented` require their sections to exist and be non-empty, and `note-class` rejects a record dated on or after `areasSince` whose topic title opens with a module the table in `.agents/notes/README.md` does not declare.
- Read the `Alternatives considered` section yourself. No check can tell whether it is honest.
- Read the module table against the records: no check can tell a reused module from a new name for the same place.

## Anti-patterns

- Recording the plan instead of the decision: "we will add X" is not a decision.
- Carrying a discussion into the record, or reconstructing an alternative after the fact: only settled conclusions belong there, and an invented alternative is worse than an empty one.
- Writing past 8 settled decisions without compressing first, or splitting a record because it got long instead of because a second module, consumer, or deliverable arrived.
- An empty or perfunctory alternatives section — it is the only part that prevents re-litigation.
- Editing a shipped record to describe a different decision.
- Keeping every record forever, so superseded facts still read as current.
- A module invented per record, so the list becomes a second title.
