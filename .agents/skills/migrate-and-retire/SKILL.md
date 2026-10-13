---
name: migrate-and-retire
description: Use when a change breaks something that has already been consumed — a published interface, a config key, a stored format, a command — or when a record, format, or artifact has stopped guiding anyone and has to be retired — to leave the reader a migration path and freeze the old generation instead of silently rewriting it.
---

# Migrate and retire

Two stations, one moment: something that other people already depend on is changing, or something that nobody depends on any more has to go. Both are governed by [evolution.md](../../../docs/evolution.md); this skill is the trigger layer that says when to apply it.

## When to use

- Your change alters an interface, a config key, a file format, or a command that has been **used outside this change**.
- You are deciding whether a break needs an upgrade guide at all.
- A record, format, or artifact has stopped guiding anyone, and you are about to delete, supersede, or archive it.
- You are about to edit something that was already sealed, published, or acknowledged.

**Exempt:** a pre-release interface whose consumers are all updated in the same change; a record that only describes a mechanical or local edit.

## How

**1. Decide the record before you write it.** Walk the selector in [evolution.md](../../../docs/evolution.md#which-record-for-which-change): a decision, an incident, a break, or a retirement. A break that nobody outside this change can feel gets no guide — write that down nowhere and save the reader the noise.

**2. Write the guide in the change that makes the break.** One guide per changed **surface**, not per release. `## Change` names the exact old behaviour, the new one, and who is affected; `## Migration` is ordered steps, each naming the exact file, key, command, or symbol, and its **last step states how to confirm the migration worked**. Over budget means the content belongs in a linked file, not in a longer guide.

**3. Keep the three version states apart.** The writer constant in code is the only authority for "what this checkout writes". The accepted compatibility baseline and the published version are separate records with separate evidence. Absence of a published record is **not** evidence that nothing was published: check whether a later release advanced it before you claim a version is unreleased.

**4. Add a successor; never rewrite a generation.** Once a format, schema, or published interface has been consumed, write a new file, directory, or version number. Never move, overwrite, rename, or delete the committed generation, and do not treat the old one as a rollback or downgrade promise. The same holds for `dev/evidence/` records: a re-run writes a successor file, because editing a record of what was true then is forging it.

**5. Retire on the standard, then seal.** Ask which artifact would make the next reader decide wrongly: delete the purely mechanical ones, merge a fully superseded record into its successor (keeping every unique reason, rejected alternative, consequence, and required verification), keep a rejected proposal only while it still prevents a tempting mistake, and move anything that still owns history into `.agents/notes/archived/<class>/` with an `Archived: <yyyy-mm-dd>` line and its `.agents/notes/archived/manifest.json` entry in the same change. **After sealing, edit nothing** — to change the content, unarchive it and write a new record, or supersede its conclusion in the current-state document.

## Verification

- `dev/upgrade-guide` and `upgrade-guide-budget` pass for every guide you added.
- `evolution-policy` passes: the policy home still carries its sections, so the rule you followed was not deleted along the way.
- `archive-seal` passes — the seal, the file's digest, its date line, and the scanned corpus all agree — and you watched it go red once for the file you sealed (remove the entry, or edit a sealed byte, then restore).
- `python3 tools/run-evidence.py --base <verified-base-ref>` records the run; the manual entries below are done, not assumed.
- Manual: a reader who has never seen the change can run the `## Migration` steps to the confirmation line, and the guide names only the surfaces their own usage touches.

## Anti-patterns

- Writing a CHANGELOG entry — "what changed" — and calling it a migration guide.
- Writing one guide that spans two releases, or a guide per release instead of per surface.
- Bumping a version number while the writer constant stays put, so two facts disagree about what ships.
- Declaring a version unreleased because no record mentions it.
- Editing a sealed record "just this once": the digest gate exists precisely because that edit is invisible in review.
- Archiving everything to avoid deciding, so the archive becomes the next reader's context.
- Keeping a rejected proposal after its verdict stopped preventing anyone from re-proposing it.
