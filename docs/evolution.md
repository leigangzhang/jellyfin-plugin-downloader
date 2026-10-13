# evolution.md

English | [中文](evolution.zh.md)

How the old thing leaves and how a breaking change leaves its reader a path. The rules hold for any repository; the commands named here are this kit's own, so every one of them really runs.

The two stations' **entry and exit** are stations ⑩/⑪ in [dev/README.md](../dev/README.md); this file owns the rules themselves. The source material is DeepSeek Harness's evolution trio (upgrade guides / persistence-change acknowledgement / the session-format status page) and the archive seal, reduced to its smallest form once the platform-specific parts were removed — bilingual pairing has its own home in [i18n.md](i18n.md).

## Which record for which change

Pick the record before you write it. One selector, and the details live in the home it names:

| What you changed | Which record to write | What holds it |
|---|---|---|
| A non-trivial decision | [.agents/notes/](../.agents/notes/implemented/TEMPLATE.md) (proposal → decision) | `decision-proposed`, `decision-implemented` |
| Something published broke | [postmortem/](../dev/postmortem/TEMPLATE.md) (including "what was green" and a negative control) | `postmortem-record` |
| An externally perceptible break | [upgrade-guide/](../dev/upgrade-guide/TEMPLATE.md) (one per surface) | `dev/upgrade-guide`, `upgrade-guide-budget` |
| A record that no longer guides anyone | Delete it, or seal it into [.agents/notes/archived/](../.agents/notes/archived/manifest.json) | `archive-seal` |
| None of the above | Write no record | — |

**One guide per break, not per version.** Guides split by **surface** (a command, a config key, a storage format, a published entry point); a version that breaks three things gets three guides. Put two surfaces in one guide and the reader has to work out which part is theirs.

## Write it in the change that breaks it

Write the upgrade guide **in the change that introduces the break**. Do not defer it to release preparation: by then the break is already in someone else's runtime, and the person who made it is no longer in that context.

- The guide answers "**what do you have to change**": `## Change` states the old behaviour → the new behaviour and who is affected; `## Migration` gives ordered steps whose **last step is how to confirm the migration worked**.
- A CHANGELOG is a different thing, and this kit produces none. "What changed" is answered by the upgrade guides and the release records; the reader cares only about what they must change. Over budget, the order of disposal is: link to the source → move the rationale into a decision record → turn repeated steps into a script (see [documentation.md](documentation.md#budgets)).

## Version state

A consumed format has **three states**, and they must be recorded separately, with no state vouching for another:

| State | Its authority | What it answers |
|---|---|---|
| What is written today | The **one hand-written writer constant** in the code | Which generation this checkout writes |
| How far compatibility is confirmed | A named checkpoint or mirror record | The baseline confirmed compatible |
| Who outside used it | The release records plus named tag evidence | Which generation was published |

Three disciplines:

- **A missing record is not evidence of non-publication.** A record proves only that someone wrote one down, not that nothing happened; before declaring a generation unpublished, confirm that no later release advanced it.
- **Only the writer constant is authoritative.** A package version, an export name, a fixture file name, or a cache version is not writer authority — restatement is drift, and the second fact diverges eventually.
- **Three states need three homes.** Writing all three into one "does the document exist" boolean means having no state at all (the most common gap at station ⑩).

The kit's own first instance is the **set of check kinds**: the writer is the `SUPPORTED_KINDS` tuple in [tools/check-invariants.py](../tools/check-invariants.py); the confirmed baseline is the verbatim mirror in [contracts/kinds.md](../dev/contracts/kinds.md), held equal by `contract-mirror`; and the published state is "never published as a package, only copied as a directory". Three facts, three homes, and none of them is "the file exists".

## Consumed artifacts are append-only

Once an artifact **has been consumed** — a format, a schema, a published interface, a sealed record — it has one direction left: **add a successor, never edit the old generation**.

- Never move, overwrite, or delete a committed generation. The old generation is neither a rollback path nor a downgrade promise.
- Cut a new generation with a **new** file, a new directory, or a new version number rather than editing the old file's content or name.
- A superseded generation stays readable but **no longer has authority**; current state is carried by the new generation.

The same holds for `dev/evidence/` records: `run-evidence.py` never overwrites an existing record, it writes a numbered successor beside it (`-2`, `-3`) — rewriting a verdict after the fact is forging a record.

## Retire or archive

Ask once per round of work: **which artifact would make the next reader, human or agent, decide wrongly?** Then dispose of it by the standard:

- **Delete outright**: an implemented record that only described a mechanical or local change.
- **Merge first, then delete**: a fully superseded record may be folded into the current one and deleted, but **every distinctive rationale, rejected alternative, consequence, and required verification must survive the merge**. Partial supersession does not count — keep both and cross-link them.
- **Keep it in `.agents/notes/rejected/`**: a rejected proposal, **only while it still prevents a tempting mistake**; once the verdict stops doing that, delete the whole record.
- **Seal it**: a record that still owns a piece of history but is no longer current moves into `.agents/notes/archived/<class>/`, carries `Archived: <yyyy-mm-dd>` in its header, and is registered in [manifest.json](../.agents/notes/archived/manifest.json).

**Sealed means never edited again.** The `archive-seal` gate makes four kinds of drift red: a changed content digest, a registered file that is gone, a file outside the registry, and a date that disagrees with the record header.

When sealed content needs to change, the right move is not "edit it a little": take it out of the archive (delete the entry and the file) and write a new record, or supersede its conclusion in a current-state document. An archive is not a dustbin; it is **frozen evidence**.

## What this file does not own

- The two stations' **entry and exit** are in [dev/README.md](../dev/README.md); the upgrade guide's **shape** is in [upgrade-guide/TEMPLATE.md](../dev/upgrade-guide/TEMPLATE.md).
- A document's **kind, budget, and publication switch** are in [documentation.md](documentation.md); the paths in this file either link there or name a gate.
- **Quality** is undecidable here: a lazily worded Migration, or an archive reason of "because it is no longer needed", passes `archive-seal`. That half belongs to [migrate-and-retire](../.agents/skills/migrate-and-retire/SKILL.md) and review.
- **"The registry and the file changed together"** is undecidable here: the gate sees only the current state of this tree, never the two being written at once. Closing that half needs a CI baseline comparison (against the pre-change registry), and this kit wires no CI.
- **History itself** is not here: why a particular break migrates this way lives in its decision record; this file states only how to dispose of things now.
- **What each retirement gate can see.** `release-record` guarantees only that a release record's five sections exist and are not empty; whether an install really happened outside the repository, whether the environment was scrubbed, and whether anything was quietly rebuilt at publish time are beyond it. `sealed-manifest` sees the manifest against the files **in this tree only**: it cannot see the two written together, and it compares no baseline, so an old seal touched relative to the previous commit is invisible. `archive-seal` with an empty `sealed` list only catches a file outside the manifest. `evolution-policy` guarantees six sections exist, not that the selector chose rightly.
