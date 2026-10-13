# Glossary

English | [中文](glossary.zh.md)

One concept, one word. This table is normative for new prose: it says what each word means here, which spelling to use, and — where this kit and the host repository disagree — what the host calls the same thing. Translations live in [i18n.md](i18n.md#terminology), and how to say a term to someone who does not share this vocabulary lives in [plain-language.md](plain-language.md).

## How to use this glossary

- Read it before naming a new concept, a new check, or a new document kind: a word that already means something here may not be reused for something else.
- Link a term by its anchor — `glossary.md#check` — instead of restating its definition.
- Each entry carries three things: what the term is, what to write instead when a sentence could be read two ways, and the host's word where the two vocabularies differ.

## How to add a term

A term earns an entry when a second document needs the same concept, when two words compete for one meaning, or when one word carries two meanings — and then it gets two qualified entries rather than one vague one. Nothing else belongs here: this is not a translation table (that is [i18n.md](i18n.md#terminology)) and not a copy of the host's vocabulary.

## Structure

### station

One step of the lifecycle, from intent to retirement ([dev/README.md](../dev/README.md)). Write `station`; `stage` is not a synonym here, and a numbered station is written `station ⑥`. Host word: the host has no stations, only packages and subsystems.

### cross-cutting station

A station that is not a step in the sequence: ⚡ incident and 📄 documentation apply whenever their subject appears.

### tier

One of the three adoption tiers — minimum, delivery, long-lived — that decide which homes and checks a copy of this kit includes. A tier is not a quality level.

### home

The one file or directory that owns a fact. "One home per fact" is the rule; every other mention links there.

### document kind

The role a document plays — `guide`, `index`, `generated`, `contract`, `record`, `skill`, `template`, `reference` — which fixes its skeleton, its budget, and its reader ([documentation.md](documentation.md)). Bare `kind` is ambiguous: write `document kind` or `check kind`.

## Checks and evidence

### check

One declared executable convention — `id`, `kind`, `patterns`, and that kind's knobs — declared in [tools/workflow.json](../tools/workflow.json) and run by [tools/check-invariants.py](../tools/check-invariants.py). Name a check by its id; never call it "the gate".

### gate

A collective noun for the checks that guard one station's exit — "the review gate", "the release gate". It never names a single executable thing: write `check` and its id. The host repository forbids the metaphor outright; this kit keeps the word only in that collective sense.

### patterns

The glob list on a check that decides which files it reads. A check whose patterns match no file is rejected rather than passed.

### surface

One entry in the `surfaces` list of [tools/workflow.json](../tools/workflow.json): a named group of paths plus the evidence commands that group requires.

### changed surface

The files a change actually touched, as [tools/change-scope.py](../tools/change-scope.py) reports them. Distinct from `surface`, which is declared, not observed.

### evidence

Two current senses: the **evidence commands** declared on a surface, which `run-evidence.py` executes; and an **evidence record** under `dev/evidence/`, which carries one verdict per command. Write the qualified form whenever a sentence could mean either.

### budget

A ceiling on a file — `maxLines`, `maxWords`, or both. Words for whitespace-separated prose, lines for Chinese prose.

### lane

How a check contributes to the verdict: a **blocking** lane can fail the change, an **observational** lane reports without blocking ([testing.md](testing.md)).

### guard

A check that exists to catch one specific regression and was watched going red for it. Not every check is a guard.

### empty corpus

The state of a check whose patterns match no file. It is a violation, never a pass: a check with no subject cannot fail.

### self-test

`check-invariants.py --self-test`: the run that proves every check kind rejects an invalid fixture and accepts a valid one.

## Records and retirement

### decision record

A record at `.agents/notes/<lifecycle>/<class>/<yyyy-mm-dd>-<module>-<slug>.md`, keeping one decision's problem, choice, rejected alternatives, consequences, and verification ([.agents/notes/README.md](../.agents/notes/README.md)). Write `decision record`; `note` alone is not a synonym. One is written only when its `## Decision` can be stated as a [`standing constraint`](#standing-constraint) that has no other home. Host word: **Agent Note**.

### standing constraint

A sentence that is true now and that a later change can be checked against — "X is owned by Y", "Z never reaches the wire". A `## Decision` that can only be written as a completed action ("we moved X") is not one, and earns no record.

### session decisions file

The working file `.agents/notes/.session-decisions/<session>.md` holding the [`standing constraints`](#standing-constraint) a session settled, each self-contained so a compacted conversation can continue from it. `.gitignore` holds the directory; `/record-decision` freezes the entries into records and deletes the file.

### proposal / rejected

The other two lifecycles of a record: designed but unbuilt, and considered and declined.

### class

The second path axis of a record — one of six closed values (`feature`, `bug-fix`, `simplification`, `architecture`, `process`, `testing`). A class is not a document kind.

### move

What happens to a record between lifecycles: the skeleton is rewritten in the same change, never appended to. Host word: the same move, described in its Agent Note rules.

### seal / archived / frozen

Three words for the retirement of a record. **Seal** is the act — the file's digest, its archive date, and a reason enter `.agents/notes/archived/manifest.json`. **Archived** is where it lives. **Frozen** is the consequence: a sealed record is never edited, reformatted, translated, repaired, or moved. Host word: `archived/`, with the same three meanings.

### pair / sidecar / triplet

**Pair**: an English canonical file and its `.zh.md` counterpart. **Sidecar**: the `.i18n.yaml` consistency record beside them. **Triplet**: all three files, which move and get re-recorded together ([i18n.md](i18n.md)).

### criterion

One acceptance entry in a record, carrying a stable id and naming the check or surface that would go red for it. A criterion with no owner is a wish.

## Capabilities and contracts

### capability

A seam, core, service, or bundle in the registry: something a consumer can depend on. Not a document kind and not a feature list.

### seam

A swappable capability with three roles: a Service Definition, one or more Service Providers, and one or more Consumers. The seam is the complete capability, never one role. Host word: the same, and the host reserves the word the same way.

### registry

The file that classifies capabilities (`dev/capabilities/registry.json`), kept in step with the `capability: <key>` markers in source.

### contract

An interface someone must honour toward its callers, living where a compiler can compare it. Prose links to it; prose never restates it.

### mirror

A block shown verbatim in a document and registered in `dev/contracts/mirrors.json`, so the paste and its source region are compared. A mirror is a projection with an exactness requirement.

### projection

Anything a generator produces from one source — the check catalog, a pairing record, an evidence record. A projection is never hand-edited: change the source and re-run the generator.

## Language and pairs

### canonical

The side of a pair that the other side is translated from: English here, and the only side a host-governed record is compared as. Not a synonym for "correct".

### counterpart

The other side of a pair — the Chinese file beside an English one.

### switcher

The `English | [中文](…)` line at the top of a paired document. It is excluded from the pair's link signature, so it may differ per side.

### identifier

A string that stays byte-identical in both languages: a path, a command, a check id, a surface name, a class, a criterion id, and a term heading in this glossary. Identifiers are the part of a document that is not translated.

### prose

The natural-language text of a document, as opposed to its identifiers, code, tables, and configuration. Prose is translated, states current state, and carries a budget.

### first occurrence

The one place in a document where a term's Chinese rendering carries its parenthetical annotation; later occurrences use the shorter form ([i18n.md](i18n.md#terminology)).

## Banned spellings

The list is executable, so it lives with the check that enforces it: `banned-spellings` in [tools/workflow.json](../tools/workflow.json), rendered into [check-catalog.md](check-catalog.md). Every entry is a spelling this repository has already stopped using; a ban is added only after the corpus is clean, so the check never ships red.
