# Proposal: <one-line title>

English | [中文](TEMPLATE.zh.md)

Status: proposed
Date: <yyyy-mm-dd, the day the topic was first proposed>
Class: feature | bug-fix | simplification | architecture | process | testing

## Problem

<The motivation, written so it stands without the solution. What breaks, for whom, and what it costs today. Facts and observations, not adjectives.>

## Proposal

<The intended change, in the future tense. Plans, migration steps, and open questions belong here while the work is unbuilt. Name the exact files, keys, interfaces, or commands it will add or change.>

## Alternatives considered

<Mandatory, and it may not be empty. One bold-led paragraph per real alternative and why it lost. Recorded, never invented: if an alternative was genuinely never on the table, say so and say why not.>

**<Alternative A>.** <Why it lost.>

**<Alternative B>.** <Why it lost.>

## Acceptance criteria

<What observable state means done. Every line must be translatable into an assertion, a command, or a snapshot — if you cannot picture the check, it is a wish, not a criterion. Give each line a stable id and name, in backticks, the check or surface that would go red for it, so the last question in dev/README.md ("if I broke this now, which check fails?") is answered next to the claim. The two below trace to real checks in tools/workflow.json; replace them.>

- [A1] `decision-proposed` rejects a proposal whose `## Alternatives considered` body is empty.
- [A2] `criteria-traced` rejects a criterion that names no declared check or surface.

## Risks

<What could go wrong, and what the change knowingly gives up. Include the condition under which this decision should be revisited.>

## Delivery stages

<Optional. When the change is too large to land at once, split it into ordered stages that are each independently shippable and testable. Delete this section for small changes.>

1. <stage> — <what it makes true>
2. <stage> — <what it makes true>
