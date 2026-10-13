---
name: define-acceptance
description: Use when writing or revising a proposal, a spec, a plan, or a task list — before implementation starts — to turn "what done looks like" into criteria that each name the check which would go red for them.
---

# Define acceptance

A criterion exists so that someone who did not write the code can decide whether it is done. If you cannot picture the check, you have written a wish.

## When to use

- Writing `## Acceptance criteria` in [.agents/notes/proposed/](../../notes/proposed/TEMPLATE.md), or moving that record to `.agents/notes/implemented/` as `## Testing`.
- Turning a spec or a plan into tasks, and deciding what each task is worth.
- Reviewing someone else's proposal: the criteria are the part most often missing.

## How

**1. Write the observable state, not the activity.** "The panel opens on a keyboard-only path" is a criterion; "implement the shortcut" is a task.

**2. Give every criterion an id and keep it.** `[A1]`, `[A2]`, next to the bullet. The id survives the rewrite into `## Testing`; a criterion that loses its id has lost its proof.

**3. Name the owner.** In backticks, cite the check or surface that would go red for this criterion — a declared `checks[].id` or `surfaces[].name` from [tools/workflow.json](../../../tools/workflow.json). This is the one line `criteria-traced` enforces, and it is the answer to dev/README.md's stage-6 question: *if I broke this now, which check fails?*

**4. Prefer a landing point you can point at.** In descending order of strength:

| Landing point | Example |
|---|---|
| A named check | `criteria-traced` rejects a criterion with no owner |
| A command | `python3 tools/change-scope.py --base <ref> --strict` exits 0 |
| A file state | `plan.json` contains one entry per criterion id |
| A golden file | the recorded output for this scenario is unchanged |

**5. Split conjunctions.** "It is fast and it is safe and it reconnects" is three criteria. One criterion, one owner, one red.

**6. Plan the tiers here, not after the code exists.** Mark which criteria need a unit test, which need a real-entry run, and which need a golden file. Criteria written after implementation describe the code that was written, not the behaviour that was asked for.

**7. When nothing can prove it, say so.** Either delete the line, or mark it `unpinned` with the reason. A criterion you admit you cannot check is honest; one phrased as "works correctly" is not.

## Verification

- `python3 tools/check-invariants.py` passes `criteria-traced`: every bullet has an id and names a declared owner.
- For each criterion, you can name the command that would fail if the behaviour regressed — and you have seen at least one such check fail once.
- No criterion uses a category ("correctly", "properly", "user-friendly") as its outcome.

## Anti-patterns

- **Categories as criteria.** "Functionally correct", "edge cases handled", "good UX" cannot be judged, so they are re-litigated every round.
- **A criterion with no owner.** It reads as done and proves nothing; every re-read of the record inherits the doubt.
- **Numbering a wish.** An id does not make a criterion checkable — `criteria-traced` passes on a well-formed wish, which is why review still reads these lines.
- **Writing the criteria after the code.** It launders the implementation into the requirement.
- **Conjunction chains.** One bullet with three outcomes is one owner covering three behaviours, and the third one will silently rot.
