---
name: shape-intent
description: Use when a request arrives as a goal rather than a change — "I want to build X", a tool, a plugin, an MVP, a product idea — and before any proposal is written, to turn it into a small set of classified proposals whose acceptance criteria each name the check that would go red.
---

# Shape an intent

Station 1 files one request under a class. Station 2 turns one change into a proposal. Neither says how a **goal** becomes them: a goal contains several decisions, and a proposal records one. This is the step between, and its only output is a closed set of proposals and their order.

## When to use

- A request arrives as a goal: "I want to build X", "we should have a tool that…", "let us ship an MVP for…".
- Before `.agents/notes/proposed/` receives its first file for that goal.
- Not for a request that already names a change — "the session list loses its scroll position" is station 2 already, so classify it and write the proposal.
- Not for a mechanical edit, and not for a bug fix: [dev/README.md](../../../dev/README.md) exempts both from the proposal.

## How

**1. State the intent so it survives the loss of its means.** Write one sentence of the form `<who> today <situation>, so <what they cannot do>`. It may not contain a tool, a language, a framework, or a product name. A means noun in the problem statement is the most common way a proposal gets bound to an implementation before anyone has decided anything; move each one to `## Alternatives considered`, which is where a means is actually judged.

**2. Classify it** with the closed set in [tools/workflow.json](../../../tools/workflow.json): `feature`, `bug-fix`, `simplification`, `architecture`, `process`, `testing`. The class follows the decision, not the files it will touch, and it sets the process weight — a `bug-fix` gets no proposal, and routing every goal through the full process is the anti-pattern station 1 names.

**3. Name the consumer, and state the cost as an observation.** An abstraction, option, or capability with no consumer in use right now is not a feature ([splitting rules](../../../dev/capabilities/TEMPLATE.md)). Write what today costs as something a reader could watch — "twelve files edited by hand every round" — never as an adjective. If neither sentence stands, what you hold is a wish, and a wish does not enter station 2.

**4. List the forks, then cut on them.** A goal forces a decision wherever two implementations would be observably different for the consumer. List those points; each is a candidate slice. Then read the result with this table.

| What you observe | What it means |
|---|---|
| Changing one slice forces a change in another | One decision: one proposal, several `## Delivery stages` |
| Each slice has its own rejected alternative | Two decisions: two proposals |
| A slice cannot state two to five decidable criteria | Not a slice: an attribute of a smaller one, or a wish |
| A slice has no consumer yet | Do not propose it yet ([splitting rules](../../../dev/capabilities/TEMPLATE.md)) |
| One slice must exist before another can | Two proposals, in dependency order |
| A slice only makes existing behaviour match what was declared | A `bug-fix`: it needs no proposal |

Do not cut by component, by screen, or by file. Those cuts produce slices whose completion conditions depend on each other, so none of them can be judged done.

**5. Write the criteria for one slice, and only for one.** Every criterion takes a stable id and names, in backticks, a declared `checks[].id` or `surfaces[].name`; if you cannot picture the check, it is a wish ([define-acceptance](../define-acceptance/SKILL.md)). This is why [tools/workflow.json](../../../tools/workflow.json) is edited **before** the proposal: a criterion can only cite a surface that already exists, and declaring it afterwards means writing every criterion twice.

**6. Record the rest as what this proposal gives up.** The slices you are not doing now go into this proposal's `## Risks` as one revisit condition each. Alternatives are recorded, never invented ([record-decision](../record-decision/SKILL.md)): you cannot yet say why the second slice's approach lost, because that decision has not been faced. Do not open a roadmap, a backlog, or a slice index for them — a speculative list is read as a plan, then as a commitment, then as a fact.

## Verification

- The intent sentence contains no means noun, and a reader who has never heard of the tool can restate it.
- Every candidate slice falls in exactly one row of the table in step 4, and you can say that row out loud in one sentence.
- The proposal you are about to write cites a declared check or surface for every criterion, and `criteria-traced` reports it green once the file exists.
- The deferred slices appear only as revisit conditions inside the current proposal: no separate document, and no list.

## Anti-patterns

- **The means in the problem statement.** "I want to build this with X" answers how, and nobody has said what breaks today.
- **Cutting by artefact.** A board, a button, and a score panel are not decisions; their completion conditions are entangled, so each is judged done by argument.
- **Proposing decisions you have not faced.** The `## Alternatives considered` sections get invented, and an invented alternative is worse than an empty one — the next reader takes it for a reason.
- **A speculative slice list.** It becomes a plan, then a commitment, then a fact nobody re-derives.
- **Declaring criteria before the surface exists.** The criteria get rewritten to match the file layout, which is the implementation laundering the requirement.
