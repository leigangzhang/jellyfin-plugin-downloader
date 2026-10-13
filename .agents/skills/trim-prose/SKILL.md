---
name: trim-prose
description: Use when auditing or fixing prose that reads like a leaked reasoning transcript — dead session citations, change narration such as "used to", stack or review vantage, reviewer-addressed justifications, control-flow narration, or hedges left over from planning.
---

# Trim leaked reasoning

Leakage is prose whose **vantage is the authoring session rather than the repository**: it cites artifacts only that session could see, narrates the change instead of the state, or argues with a reviewer who has left.

**The fix is never deletion alone** when a passage carries factual clauses: restate each so it stands on its own, then delete the transcript around it. A passage carrying none — an audit code, control-flow narration — is deleted outright.

## When to use

- Auditing comments, JSDoc, READMEs, or decision records.
- After an agent has written prose, before accepting it.
- Any time a document references something a fresh reader cannot resolve.

## How

**The one test.** For every suspect passage ask: *could a reader at the current revision, with no access to any session transcript, review thread, or uncommitted draft, resolve every reference and verify every claim?* If no, restate the surviving facts and delete the rest. If yes, it is not leakage — but a resolvable **change story** is still change narration on a current-state surface.

**Taxonomy — what to remove:**

1. **Dead session citations** — `(decision 7)`, `(audit C2)`, `plan §1.4`, phase labels. If the decision has a committed owner, cite it by name and path; otherwise delete the citation and restate its fact.
2. **Stack and review vantage** — "a later change in this series", "this change adds", "the previous commit". State the shipped mechanism; deferred work becomes a `TODO`.
3. **Change narration** — "used to", "no longer", "the old X", indexical stamps ("v1", "today", "this cut"). State the present behaviour. A fixed regression becomes a present-tense counterfactual: "without X, Y happens".
4. **Review choreography** — "rejected in review", "the reviewer confirmed". Keep the decision as plain fact; delete who said it when.
5. **Reviewer-addressed justification** — "this is safe because…", "the cast is fine — it simply…". State the invariant that makes it safe, or delete the comment.
6. **Derivation transcripts** — "first we X, then we Y", test walkthroughs, proofs of obvious branches. Keep only a non-obvious contract.
7. **Hedges and planning residue** — "probably fine for now", "should be enough". Promote to a `TODO` or restate the actual bound.
8. **Authoring-language slips** — untranslated fragments in prose whose language is otherwise something else.

**What is *not* leakage — do not delete these:**

- issue and ticket references: they resolve for any reader;
- citations inside a decision record's change-story section;
- suppression justifications (`lint-disable -- reason`, ignored-coverage reasons, empty-catch explanations);
- present-tense counterfactuals that pin a regression;
- measured bounds calibrating a constant;
- runtime old/new states ("the old connection drains before the new one accepts" — that is lifecycle, not history).

**Calibrate every probe before trusting it.** This is the step that gets skipped. Before running a search pattern, confirm it matches a **known positive** and does **not** match a **near-miss negative**. A pattern that silently matches nothing looks exactly like a clean corpus, and a pattern that matches everything gets ignored. Read the densest prose in scope without a pattern in hand as well: the probes are aids, not the definition.

## Verification

- Re-run the probes: the only remaining hits are sanctioned keeps.
- Every remaining citation resolves at the current revision.
- Run the checks for the touched surfaces: `python3 tools/check-invariants.py`.

## Anti-patterns

- Deleting a factual clause because the sentence around it was narration.
- Trimming an obligation into an endorsement, or a hypothetical into a shipped feature.
- Removing a measured bound and leaving the constant unexplained.
- Trusting a search that was never calibrated against a known positive.
- Reading only the hits and never the surrounding prose.
