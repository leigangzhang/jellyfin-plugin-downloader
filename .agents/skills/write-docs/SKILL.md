---
name: write-docs
description: Use when creating, restructuring, auditing, or reviewing documentation, READMEs, generated references, or comments — including before publishing a page, giving a document a kind, or claiming that a documented command, default, or error behaves as written.
---

# Write documentation

**The only valid evidence for an operational claim is that you ran it.** Everything below follows from that. The rules live in [documentation.md](../../../docs/documentation.md); this skill answers only "when do I apply which rule".

**Name the concept before you write it.** A word that already means something in this kit may not be reused for something else: check [docs/glossary.md](../../../docs/glossary.md), and if the word is new, add the entry in the same change.

## When to use

- Writing or restructuring a README, a guide, a reference page, or a generated page.
- Reviewing documentation, including your own.
- Giving a document a kind, a budget, or a publication decision.
- About to write down a command, a default, an error, or a platform difference.

## How

**1. Classify before writing.** Every document is one of two things, by **purpose**:

- **tutorial**: an ordered path to a result, introducing only the concepts each step needs right then;
- **reference**: a lookup scope describing current behaviour, with no teaching order.

Substantial as both means two documents. Classification comes first, then the kind ([kind table](../../../docs/documentation.md#document-kinds)) — the kind fixes the skeleton, the budget, and the reader.

**2. Put it in its nearest owner.** A package contract sits next to the package code; cross-package learning goes into `docs/`. One home per fact: when a sentence appears twice, delete one copy and link the other. The placement table is in [one home per fact](../../../docs/documentation.md#one-home-per-fact).

**3. Decide link, mirror, or generate first.** Default to a link: the fact lives in source or config, and the document says what it is and where to look. Mirror only when it must be shown verbatim, and register it in `dev/contracts/mirrors.json`. Generate only when a whole page is exported from one source; change the generator rather than the page, and give it a `--check` freshness command. Restatement is drift.

**4. Verify by executing.**

- Run every command, config snippet, and example the way the page will present it; write down only what you observed, including the exact output and the failure mode.
- **Delete anything you cannot reproduce.** Do not carry a command, a default, or a behaviour over from memory, from analogy, or from a neighbouring page.
- When an assertion fails to reproduce, **change the assertion, not the test**.

**5. Write from the reader's side.**

- Open with what the reader can **do** with it, not with what it is internally.
- Current state only. History lives in commits and decision records: no "it used to be", no "this version changed it".
- Do not narrate control flow, tests, or your own reasoning path.

**6. Work through a budget in order.** Over budget: **relocate → condense → raise the number last**, and say why when you raise it. A Chinese standing document counts lines and an English page counts words ([Budgets](../../../docs/documentation.md#budgets)).

**7. Publication is an explicit decision.** A new page must be classified `public` or `internal` in `docs/publish.json`; a file that is neither is a silent omission and the gate goes red. The manifest is the switch, not the projection.

**8. Walk the slop checklist before handing it over.** Duplicated rules, history out of bounds, status annotations, hand-copied catalogs, reasoning-trace leakage, paragraph walls, emphasis inflation ([The slop checklist](../../../docs/documentation.md#the-slop-checklist)); whatever can be pinned as a `forbidden-regex`, pin.

## Verification

- Every command on the page has been run, and its result is cited.
- Links resolve and budgets hold: `python3 tools/check-invariants.py`.
- The generated page was not hand-edited: `python3 tools/gen-docs.py --check` (or your own generator's `--check`).
- A new page is classified in `docs/publish.json`, and the publication set contains no broken promise.
- If the reading surface changed, re-read the text the agent actually sees (the `agent-inputs` surface in `tools/workflow.json`).
- If the page is paired, both sides moved together and `python3 tools/pair-docs.py --check` is green ([i18n.md](../../../docs/i18n.md)).

## Anti-patterns

- A summary that states the subject's internal identity instead of its use.
- A command copied from a neighbouring page.
- A document that describes this change rather than the current state.
- Hand-editing a generated page, or changing the source without re-running the generator.
- Assuming a page ships because it exists.
- Raising a budget number instead of moving content to the home it belongs in.
- Editing one side of a pair and leaving the other for later.
