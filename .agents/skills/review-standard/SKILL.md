---
name: review-standard
description: Use when reviewing a change — your own before you ship it, or an agent's before you accept it — to apply a consistent bar instead of re-deriving what good looks like every time.
---

# Review standard

A short review with one substantiated blocker beats a list of nits. Review is not a style pass: it is where the classes of defect no gate can see get caught.

## When to use

- An agent reports work as complete.
- You are about to accept your own change.
- A gate is green and you are tempted to stop there.

## How

**Blocking requirements.** Any one of these fails the review regardless of everything else.

1. **The reviewer is not the author.** Self-review by the same model that wrote the change repeats one set of blind spots twice. If no independent reviewer exists, say so — and say which requirements below you could not check.
2. **Every claim has external evidence.** Commands run, output quoted, file state read back from outside the writer. Not summaries, not "looks right".
3. **The real entry path was exercised.** A hand-mounted test does not prove the shipped artifact loads; a unit test does not prove the command runs.
4. **Contracts and their documentation moved together.** A changed default, error, key, or visible string updates its owning document in the same change.
5. **Registrations clean up.** Every contribution that adds a listener, handler, cache, or resource proves its disposal.
6. **Enforcement sits where the decision is made.** Prompt instructions, schema omissions, and wrappers are not enforcement when a caller can bypass them.
7. **New guards can fail.** The change that adds a check also shows that check rejecting an invalid case.

**Manual checks — the ones only reading the code can catch:**

- Trace both sides of every changed interface: what it promises, and what it now does.
- Lifecycle and teardown: does destruction reach quiescence, or only request it?
- Concurrency: is there a window before publication, or a race between an await and state?
- Error paths: are orthogonal outcomes (timeout, signal, exit code) reported separately?
- Bounds: do the limits hold at the exact boundary, including multi-byte input?
- Scope: is every abstraction, option, or compatibility path tied to a current consumer?
- Deliberate risk: what does this accept, and when should that be revisited?

## Verification

Report findings as **defect, location, impact, evidence**. Separate blockers from suggestions. Omit anything a green gate already enforces. If nothing was found, say so explicitly rather than leaving the section empty.

Then give every finding a destination in `## Follow-up` — one of three, never blank:

1. **A check** — a `tools/workflow.json` entry, carrying its negative control in the same change.
2. **A rule** — the file and section that now states it, for the classes a check cannot judge.
3. **Deliberately nothing** — say why (a one-off, not reproducible, or a check that would cost more to maintain than the defect it catches).

Choosing the third is allowed. Leaving it unwritten is not: a finding with no destination is a finding that will be rediscovered.

## Anti-patterns

- A list of nits with no blocker.
- Restating the author's summary as if it were evidence.
- Accepting "a subagent verified it" without re-running the check.
- Reviewing the diff and never the artifact.
- Fixing a finding without asking whether a check should have caught it, and whether one can.
