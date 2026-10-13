# Skills

English | [中文](README.zh.md)

Each directory here is one trigger layer: a single `SKILL.md` an agent loads only when its own condition matches. This page is a way in — which one to reach for — and deliberately not a list of when each one loads, because that is each skill's `description`, the text the loader reads.

## Using one

An agent loads a skill by name once its `description` matches the work; a person types `/name`, which injects the same instructions into that step. Skills are loaded, not read in order, so the table below is a way in rather than a sequence. Every skill carries the same four sections — `## When to use`, `## How`, `## Verification`, `## Anti-patterns` — and one file serves both readers, so a skill is never translated ([i18n.md](../../docs/i18n.md#scope-and-exclusions)).

## Which one to reach for

| The question you have | Skill | What it gives you |
|---|---|---|
| This arrived as a goal, not a change | [`shape-intent`](shape-intent/SKILL.md) | the closed set of proposals a goal becomes, and their order |
| What does done mean? | [`define-acceptance`](define-acceptance/SKILL.md) | criteria a check can go red for |
| Freeze what this session settled | [`record-decision`](record-decision/SKILL.md) | the settled decisions written into records, and the file they lived in deleted |
| Which checks does this change need? | [`pick-evidence`](pick-evidence/SKILL.md) | the smallest evidence that covers the change, and the order to run it in |
| Is this test worth anything? | [`engineer-tests`](engineer-tests/SKILL.md) | the tier a case belongs to, and the determinism it owes |
| Can I say this is verified? | [`apply-distrust`](apply-distrust/SKILL.md) | the questions that separate an observation from a self-report |
| Is this change ready? | [`review-standard`](review-standard/SKILL.md) | what a review has to find, and what its record has to carry |
| How do I land it? | [`land-changes`](land-changes/SKILL.md) | preflight, dependency order, and post-landing confirmation |
| Something already consumed this, and it broke | [`migrate-and-retire`](migrate-and-retire/SKILL.md) | the successor, the upgrade guide, and the seal |
| Publish it | [`cut-release`](cut-release/SKILL.md) | release evidence taken from outside the repository |
| Write or restructure documentation | [`write-docs`](write-docs/SKILL.md) | kinds, placement, budgets, and the publication switch |
| Write to a person | [`plain-language`](plain-language/SKILL.md) | when a term has to be expanded, and how |
| This prose reads like a leaked transcript | [`trim-prose`](trim-prose/SKILL.md) | what to cut, and what has to survive the cut |
| Make it faster | [`measure-perf`](measure-perf/SKILL.md) | a measurement before the change, and a gate after it |

## Related

- Every home in this repository, and what each one owns: [docs/README.md](../../docs/README.md).
- What a skill must carry, and what makes it load: gates `skill-record` and `skill-trigger` in [tools/workflow.json](../../tools/workflow.json).
