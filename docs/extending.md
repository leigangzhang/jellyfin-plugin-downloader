# Extending the kit

English | [中文](extending.zh.md)

Everything this kit enforces is data in one file plus five scripts. This page is the how-to for changing the kit itself: which edit a given goal needs, and how to know it worked. [documentation.md](documentation.md) owns the document kinds, [i18n.md](i18n.md) owns the pairing rules, and [tools/AGENTS.md](../tools/AGENTS.md) owns the tool rules.

## Before you start

- The single configuration is [tools/workflow.json](../tools/workflow.json): the surfaces, the checks, the evidence commands, and the pairing scope.
- A data-shaped extension — a surface, a record check, a skill, a budget — must not touch code. Only a genuinely new **decision rule** changes [tools/check-invariants.py](../tools/check-invariants.py), and that one ships with its fixtures.
- A change to the kit is itself a change to a designed system, so it follows the same stations: a proposal, a decision record, and the evidence that ran.

## Data-shaped changes

1. **A changed surface** — add an entry to `surfaces` in [tools/workflow.json](../tools/workflow.json). `name` is unique, each `dev/evidence` entry must be a command a reader can paste and run rather than a description, and the optional `exclude` removes paths the `patterns` would otherwise claim (no glob expresses negation).
2. **A record check** — add a `checks` entry, then create the file it matches. A check whose patterns match no file is void, so the template comes first.
3. **A skill** — create `.agents/skills/<name>/SKILL.md` with the four sections `skill-record` requires, and a `description` that states the trigger instead of summarising the contents.
4. **A budget** — give the check `maxLines` or `maxWords`, at least one. Only whitespace-separated prose suits a word count; a standing document in Chinese is counted in lines, and a text with no word separator is undercounted by any word unit.
5. **A contract mirror** — add the `begin`/`end` markers in the source, paste the block into the document, and register it in [contracts/mirrors.json](../dev/contracts/mirrors.json). All three land in one change, or `contract-mirror` is red.
6. **A capability** — add the leading `# capability: <key>` line in source, write the registry entry, and, for a seam, fill in the Definition, Provider, and Consumer paths. Discovery is automatic; classification is hand-written.
7. **A traceable criterion** — write `- [A<n>] \`<declared check id or surface>\` <observable result>` in the acceptance section of a [proposed record](../.agents/notes/proposed/TEMPLATE.md). File it under the class that matches the decision, not the file it touches; the id survives the move into the implemented skeleton.
8. **A publication decision** — classify the file in [docs/publish.json](publish.json). `public` names files one by one and accepts no glob.
9. **A generated page** — write the generator and its `--check` mode, then declare that command on the `generated-docs` surface. A generated page is read-only; the generator is the source.
10. **A document** — give it a kind and a home, add a `required-sections` check when it is a policy home, classify it, and pair it ([i18n.md](i18n.md)).
11. **A record home** — a directory that carries rules of its own gets a `README.md` as its landing page, an `AGENTS.md` for the rules that are specific to it, or both; a directory holding nothing but a template needs neither.

## A new decision rule

Changing how a check decides is a four-place change, and all four land together:

1. the runner in [tools/check-invariants.py](../tools/check-invariants.py);
2. its registration in `RUNNERS` and `SUPPORTED_KINDS`;
3. a `--self-test` case with a positive and a negative fixture, plus one probe per distinct rule it adds;
4. the mirrored paste in [contracts/kinds.md](../dev/contracts/kinds.md), which `contract-mirror` compares against the marked source region.

A rule no fixture can make red is noise, not a guard.

## Syncing a change into a copy of the kit

A project that adopted the kit runs the same scripts and a **different** configuration, so a kit change reaches it by rule or by hunk — never by copying whole files. `diff` first, and ask of each file whether it is meant to be byte-identical on both sides.

- **The kit's files** — the five scripts, [tools/kitcheck/](../tools/kitcheck/), and the nested `AGENTS.md` files carry no project facts; copy them verbatim.
- **The project's files** — the root `AGENTS.md` names that project's own documents under `## Read first` and its own checks under `## Commands`; [tools/workflow.json](../tools/workflow.json) holds that project's surfaces, evidence commands, and policy, including whether a page deliverable needs a visual read (`delivery.page`). A wholesale copy replaces both with the kit's template.

The loss is quiet, which is why the rule is written down: the gates still pass, because an evidence entry left as the kit's placeholder reads as a manual entry and a policy that is gone reads as "this project declares nothing". It surfaces later, at the check that never ran.

## How to know it worked

Run the commands in [AGENTS.md](../AGENTS.md#commands): `--self-test` proves every guard can fail, the real scan proves the conventions hold on this tree, each generator's `--check` proves its page is a current projection, and `pair-docs.py --check` proves every pair is complete and in step. Then run `run-evidence.py` so the run is recorded rather than asserted, and read the record before calling the change done.
