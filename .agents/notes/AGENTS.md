# AGENTS.md — decision records

Decision records are this kit's RFCs: they keep the rationale, the rejected alternatives, the consequences, and the required verification. Follow the [documentation standard](../../docs/documentation.md) and the [record rules](README.md).

**A record moves; it is not renamed.** Moving a proposal into `implemented/` rewrites the skeleton (`## Proposal` becomes `## Decision`; acceptance criteria and risks fold into `## Consequences`) and keeps every criterion id; a record that still carries proposal-era headings fails `decision-implemented`.

**A record's class is path-encoded.** It lives at `.agents/notes/<lifecycle>/<class>/<yyyy-mm-dd>-<module>-<slug>.md`, with the class taken from the closed set in [workflow.json](../../tools/workflow.json); gate `note-class` rejects a folder outside the set and a `Class:` line that disagrees with its folder. Pick the class by the decision, not by the files it touches ([meanings](README.md#classification)).

**Facts may be corrected in place; the decision may not.** Paths, defaults, and mechanisms are updated in the change that alters them. Reversing a decision means writing a new record and cross-linking it.

**A record that no longer guides anyone is retired, not accumulated.** Delete a mechanical one, reject an obsolete proposal, or seal an implemented record into `.agents/notes/archived/` with an `Archived:` date; the selector lives in [evolution.md](../../docs/evolution.md).

**Both sides move together.** A record is a bilingual pair, so an edit to one side lands with the matching edit to the other and a re-recorded sidecar ([i18n.md](../../docs/i18n.md)).

**An acceptance criterion whose check cannot go red is a wish.** If no command or observation would fail when the criterion is violated, delete it or mark it `unpinned` in the record — prose that reads as verified without one is worse than an admitted gap ([skill](../skills/define-acceptance/SKILL.md)).

**A deletion of a committed path is a change like any other.** Removing or moving a record still claims a surface, and its inbound links must be resolved in the same change: a generated record is excluded from prose and pairing, but never from ownership. Gate `change-scope` reads the change, not the directory you tidied.

**Say what is not verified.** An entry that did not run, and a claim nothing can falsify, are named in the place a reader looks for the verified ones; an unverified item presented as verified is an overclaim, not a shorter report.

**Every `## Decision` sentence points at evidence.** A sentence that names no file, command, or commit is deleted or marked `unverified`; a record whose alternatives were reconstructed after the fact is fiction ([trigger](../skills/record-decision/SKILL.md)).
