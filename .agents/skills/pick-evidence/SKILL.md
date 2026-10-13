---
name: pick-evidence
description: Use before committing, pushing, or saying the work is done — and whenever a check fails and the failure looks environmental — to select the smallest set of checks that actually covers this change instead of reflexively running everything.
---

# Pick evidence

There is no universal local baseline. The checks that matter are the ones **this change can break**, so name the change first and the evidence second.

## When to use

- Before a commit, a push, or any statement that the work is done.
- After rewriting history (rebase, amend, squash): every earlier "it passed" is now void.
- When a check fails and you are about to call the failure environmental.

## How

**1. Print the change scope before choosing anything.**

```sh
python3 tools/change-scope.py --base <verified-base-ref>
```

Never guess the base. A wrong base makes the whole report fiction.

Then answer two questions before anything else: is a settled decision still unfrozen ([record-decision](../record-decision/SKILL.md))? And does this delivery unit have its one evidence record?

**2. Map each changed surface to its smallest sufficient evidence.**

| What changed | Evidence |
|---|---|
| Behaviour in one module | that module's owning test, filtered by name |
| A contract several callers read | the owning test plus each caller's test |
| Model- or user-visible output | the golden or snapshot case that owns that output |
| Documentation or decision records | `python3 tools/check-invariants.py` |
| A guard, a check, or the workflow config | `python3 tools/check-invariants.py --self-test` |
| Packaging, entry points, or build config | one clean install outside the repository, then run it |

**3. Order the selection by cost, and stop when a cheap check has already decided.**

Cost and veto power are different axes, and the cheapest checks are usually the ones that can kill the whole change. Run them in this order:

| Order | Evidence | Why here |
|---|---|---|
| 1 | Whole-tree record checks (`check-invariants.py`) | Seconds, no environment, covers every declared convention at once |
| 2 | The narrowest owning test, filtered by name | One file, one behaviour, still no build |
| 3 | Anything needing a build, a browser, a network, or a clean install | Slowest and most environment-dependent |

Once a cheap check is red, stop and fix it before paying for the rest. The reverse does **not** hold: cheap checks passing is not a reason to skip the expensive ones, because they answer different questions. A record in which an expensive command ran before a cheap one failed is a record of wasted time.

**4. Run the selected checks once, and report them as you run them.** Do not repeat a check whose inputs have not changed, and do not run the full suite merely because a commit is coming. The evidence reaches a reviewer in the reply; `python3 tools/run-evidence.py --base <ref> --record` leaves the delivery's one durable record, and a second run of the same delivery with the same verdict leaves nothing. While the work is still moving, narrow instead of repeating: `python3 tools/check-invariants.py --changed <ref>` runs only the checks whose patterns read what you just changed.

**5. When a check fails, prove the environment before blaming it.** Record the exact command, the exact failure, and why the other environment is expected to differ. "It passes locally" is not a proof; it is the hypothesis.

**6. After any rewritten history, re-run the affected evidence.** Old commit IDs, old review comments, and old "resolved" states are not current evidence.

## Verification

- `change-scope.py --strict` exits 0: every changed path matches a declared surface.
- Every command reported was actually executed, and its output is quoted, not summarized.
- No check ran twice without a change to its inputs.
- No expensive command ran after a cheap one had already failed.

## Anti-patterns

- Running everything "to be safe" — it hides which check actually covers the change.
- Running the slow, trusted checks first, then discovering a convention violation in seconds.
- Reporting intent instead of output: "tests pass" without the command and its result.
- Treating an environment claim as an explanation rather than as a claim needing evidence.
- Repeating a passing check because a push follows.
