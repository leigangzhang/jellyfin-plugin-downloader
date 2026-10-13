# Review: <what was reviewed> (<date>)

English | [中文](TEMPLATE.zh.md)

## Claim

<One sentence: what this change claims to do. Written so a reader can check it without reading the diff.>

## Evidence

<External observations only: commands run, their output, file states read back from outside the writer, screenshots, exit codes. "I read it and it looks right" is not evidence, and neither is the author's own summary of what the code does.>

- `<exact command>` → <what you observed>

## Findings

<Each finding states the defect, its location, its impact, and its evidence. Separate blockers from suggestions. If there are none, write "None" — but only after looking for the classes that code alone cannot show: lifecycle and teardown, concurrency, error paths, bounds at the exact limit, and the real entry path.>

- **Blocker** — <defect>. Location: <file:line>. Impact: <what breaks>. Evidence: <how you know>.
- **Suggestion** — <defect>. Location: <file:line>.

## Follow-up

<Where each finding went. A finding that changes nothing is a finding that will be rediscovered, so give every one a destination:>

- **Became a check** — `<check id>` in `tools/workflow.json`. The same change carries its negative control, so `--self-test` can prove it fails.
- **Became a rule** — <file and section>, for the classes a check cannot judge.
- **Deliberately nothing** — <why>: a one-off, not reproducible, or a check that would cost more to maintain than the defect it catches.

## Deliberate risks

<What this change knowingly accepts, and the condition under which it should be revisited. An empty section here usually means the risks were not looked for.>

---

<!-- The reviewer must not be the author. Same-source review is not review:
     it repeats one set of blind spots twice and calls it confirmation.
     A short review with one substantiated blocker beats a list of nits. -->
