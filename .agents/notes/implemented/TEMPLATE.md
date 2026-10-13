# Decision: <one-line title>

English | [中文](TEMPLATE.zh.md)

Status: implemented
Date: <yyyy-mm-dd, the day the topic was first proposed>
Class: feature | bug-fix | simplification | architecture | process | testing

## Problem

<The motivation, kept from the proposal. It must still stand without the solution.>

## Decision

<What is now true, in the present tense. Name the exact files, keys, defaults, events, or commands that carry the decision, so a reader can check every sentence against the code. This whole record is kept current with what actually shipped: when a path, symbol, or default changes, update it here.>

## Alternatives considered

<Mandatory, and it may not be empty. Kept from the proposal, with what was learned since. One bold-led paragraph per real alternative and why it lost.>

**<Alternative A>.** <Why it lost.>

**<Alternative B>.** <Why it lost.>

## Consequences

<What the decision bought and what it cost. The proposal's acceptance criteria and risks are folded in here as present-tense fact: what is now impossible, unsupported, or deliberately left out, plus the condition under which this decision should be revisited.>

## Testing

<What now proves the behaviour, by exact file or command, in the present tense. Keep the ids from the proposal and keep each one next to the check or surface that would go red for it; a criterion that lost its id lost its proof, and `criteria-traced` rejects it. If nothing pins a criterion, write "unpinned" and say why that is acceptable. The two below trace to real checks in tools/workflow.json; replace them.>

- [A1] `no-proposal-era-headings` rejects an implemented record that still carries `## Acceptance criteria`.
- [A2] `skill-record` rejects a skill missing one of its four required sections.

---

<!-- Moving a record from proposed/ to implemented/ is not a rename.
     The skeleton changes: ## Proposal becomes ## Decision, and
     ## Acceptance criteria plus ## Risks fold into ## Consequences.
     A record that still carries proposal-era headings fails decision-implemented.
     Facts may be corrected in place; the decision itself may not be rewritten.
     To reverse a decision, write a new record and cross-link both. -->
