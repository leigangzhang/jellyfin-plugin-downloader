---
name: apply-distrust
description: Use when about to declare a change verified or done, when a passing check is the only evidence you have, or when writing a guard or an acceptance criterion — to test each "it works" against what would go red instead of accepting the signal.
---

# Apply the default distrust orientation

The failures that survive review rarely look like failures. They look like a green check, a confident report, a stable snapshot, or the absence of an error. Distrust those signals by default, and spend the distrust only where a value crosses a boundary or a signal can lie.

## When to use

- Before saying "done", "verified", or "tests pass".
- When the only evidence for a claim is the claim itself, a single signal, or a missing error.
- When adding a guard, a check, or an acceptance criterion.
- When a sub-agent, a tool, or a reviewer reports that something is already handled.

## How

**1. Answer the eight questions before delivery.** A "no" is unfinished work, not a caveat to mention.

| # | Question | What it catches |
|---|---|---|
| 1 | Who says this passed — did I run it myself? | the self-report |
| 2 | Am I reading external state, or the component's own output? | self-certification |
| 3 | Does this test take the path the real release takes? | the substitute path |
| 4 | Is this conclusion one signal, or several independent ones? | single-signal attribution |
| 5 | If I deliberately broke the feature, which check would go red? | the shell guard |
| 6 | Am I recording correct behaviour, or current behaviour? | a snapshot freezing a regression |
| 7 | Does every surface this change touched have its smallest evidence? | the untested surface |
| 8 | Is what I wrote a contract, or my thinking process? | context pollution |

**2. Route each distrust to the file that already owns the answer.** This skill decides *when* to distrust; those files decide *how* to respond, and their rules are not restated here.

| Do not trust | Owner |
|---|---|
| A report, a summary, a sub-agent's "handled" | [testing.md](../../../docs/testing.md#tiers) — re-run the command and read external state |
| A green test | [testing.md](../../../docs/testing.md#tiers) — confirm it takes the real entry path |
| Coverage, or a mock | [testing.md](../../../docs/testing.md#tiers) — name what the check cannot prove |
| A refreshed golden file | [testing.md](../../../docs/testing.md#evidence-per-change) — read the diff as a behaviour change |
| A guard nobody watched fail | [testing.md](../../../docs/testing.md#prove-a-new-guard) — introduce the regression, watch it go red |
| One signal standing in for a result | [review/TEMPLATE.md](../../../dev/review/TEMPLATE.md) — `## Evidence` is an external observation, and the facts must agree |
| A missing signal | [testing.md](../../../docs/testing.md#tiers) — `UNKNOWN` is never a pass |
| A claim that will not reproduce | [write-docs](../write-docs/SKILL.md) — fix the claim, not the check |
| An incident | [postmortem/TEMPLATE.md](../../../dev/postmortem/TEMPLATE.md) — record what was green, then add the guard |
| A reviewer's diagnosis | [review-standard](../review-standard/SKILL.md) — verify the claim against the code before acting |
| "It's the environment" | [pick-evidence](../pick-evidence/SKILL.md) — prove it with the exact command and failure |

**3. Do not over-distrust.** A guard with no incident behind it is a guess, and it costs maintenance forever. Leave these alone:

- Values a static type already guarantees inside one process — the type is the check.
- An abstraction, option, or compatibility path with no current consumer — reject it instead of defending it.
- An uncovered line — usually dead code to delete, not a test to add.
- A check that already passed, when none of its inputs changed.
- A line or word ceiling — a guardrail, not a reduction target.

**4. Landing a rule.** A rule that cannot go red is a wish. Add it to [tools/workflow.json](../../../tools/workflow.json), prove it rejects an invalid fixture in `--self-test`, and watch the real violation fail once before trusting it.

## Verification

- All eight questions are answered with an observed command or file, not an intention.
- Every guard this change added has been watched red, then green.
- No evidence in the record is the component's own report about itself.
- Each distrust above points at the file that owns it; no rule is stated twice.

## Anti-patterns

- "Tests pass" with no command and no output.
- Accepting a sub-agent's or a reviewer's summary as the observation.
- Reading a missing error as success.
- Adding a guard nobody has watched fail.
- Trusting an elegant explanation over the trace that contradicts it.
- Defending internal, typed, consumer-less surfaces "just in case".
