# The kit's scripts

English | [中文](README.zh.md)

Five zero-dependency Python 3 scripts under `tools/` are this kit's whole implementation; everything else is Markdown and JSON. [workflow.json](workflow.json) is their only configuration, and [AGENTS.md](AGENTS.md) owns the rules for changing them. Each script's module docstring is its complete reference — `python3 tools/<script> --help` prints it — so this page states what each one answers, how to read a result, and where it stops.

## Directory structure

| Path | What it is |
|---|---|
| `check-invariants.py` | the conventions runner (thin entry) |
| `kitcheck/` | the engine package behind `check-invariants.py`; the next section lists its modules |
| `gen-docs.py` | the catalog generator |
| `pair-docs.py` | the pairing recorder and checker |
| `run-evidence.py` | the evidence runner |
| `change-scope.py` | the surface mapper |
| `workflow.json` | the only configuration: surfaces, checks, evidence commands, pairing scope |
| `tiers.json` | the only tier switch: which stages this project runs |
| `AGENTS.md` | the rules for changing anything here |
| `README.md` | this page |

### Kitcheck modules

The package behind `check-invariants.py`, one module per subject. A new guard goes in the module that owns its subject; a new subject is a new module ([rules](AGENTS.md)).

| Module | What it owns |
|---|---|
| `core.py` | shared helpers: configuration, file iteration, counts, the kinds constant |
| `policies.py` | budgets, required sections, forbidden patterns |
| `records.py` | the note-class guard: folder, `Class:` line, and the closed set must agree |
| `publication.py` | every document classified exactly once |
| `seals.py` | a sealed record is frozen; seal and record name the same date |
| `criteria.py` | every criterion cites a declared check or surface |
| `mirrors.py` | a pasted block bound to its source region |
| `capabilities.py` | the registry and the source markers must agree |
| `skills.py` | a description is a trigger, not a summary |
| `tiers.py` | the declared tiers, and the check that proves the tree agrees |
| `registry.py` | the kinds, the runners, and the self-test |
| `cli.py` | the command line |
| `__init__.py` | the package surface |

## The five scripts

| Script | It answers | Canonical run |
|---|---|---|
| `change-scope.py` | What changed, which surfaces it hits, and what evidence each surface needs | `python3 tools/change-scope.py --base <ref>` |
| `run-evidence.py` | Really runs that evidence and records one verdict per command | `python3 tools/run-evidence.py --base <ref>` |
| `check-invariants.py` | Which executable conventions hold right now, and whether each guard can fail; `--changed <ref>` runs only the checks whose patterns read that change, which is the iteration mode, while the full set is the boundary run | `python3 tools/check-invariants.py --self-test` · `python3 tools/check-invariants.py --changed <ref>` |
| `gen-docs.py` | Whether the committed [check catalog](../docs/check-catalog.md) is still a current projection | `python3 tools/gen-docs.py --check` |
| `pair-docs.py` | Whether every bilingual pair is complete and in step | `python3 tools/pair-docs.py --check` |

`change-scope.py` is read-only and never guesses its base: it prints the changed paths, the evidence each surface requires, and every path with no declared owner. `--strict` turns "a new file nobody owns" into a failure, which is what makes it usable in CI. The other four each have a `--check` mode, and those modes are the evidence entries declared on the surfaces in [workflow.json](workflow.json).

## Reading a result

`run-evidence.py` gives every command one of three verdicts, so "the change is broken" and "the tool is broken" stop collapsing into one signal:

| Verdict | Criterion | Counts as passed |
|---|---|---|
| `PASS` | Parsed, ran, exit 0 | yes |
| `FAIL` | Parsed, ran, non-zero exit — a verdict on the change | no |
| `UNKNOWN` | Never ran: unresolvable at execution time, shell 126/127, a process that will not start | **no** |

Undecidable is never a pass. Manual entries are listed separately as "you must still do these" and are never counted as verified, and an existing record is never overwritten: a later run on the same day and branch writes a numbered successor beside it. [testing.md](../docs/testing.md) owns the tier policy that decides which commands exist in the first place.

## Known limits

The five scripts — and the checks `check-invariants.py` runs — guarantee exactly what is written here, and nothing more. Each entry is deliberate.

- `change-scope.py` reads the working tree with `git status --porcelain --untracked-files=all`, and git quotes paths containing special characters; a path under an ignored tree does not appear at all. Use `--json` when you need a machine interface.
- `run-evidence.py` sees only the runner's layer: when the interpreter starts but the script it was asked to open is gone, the exit code is the interpreter's own, indistinguishable from an assertion failure, and it is recorded as `FAIL`. The conservative direction is the right one — an extra `FAIL` costs a wasted run, a missed `PASS` is a disaster.
- `run-evidence.py` never overwrites a record: a second run on the same day and branch writes `-2`, `-3` successors. Nothing guards a hand-edited old record either; freezing generated output rests on history and review.
- `check-invariants.py` covers path-shaped facts: counts, regexes, digests, and registration sets. It cannot judge semantic invariants; those need a project test declared as that surface's evidence.
- `criteria-traced` guarantees that every acceptance entry carries an id and names an existing check or surface. It cannot judge whether a criterion is really decidable or whether the owner it names is the right one, and it does not check id uniqueness — a well-formed wish passes.
- `capability-registry` discovers existence from markers rather than by parsing a language, so `discover.patterns` must stay narrow enough to cover only the source files that own a capability; a document or a fixture can produce a false declaration. Its set of kinds is closed, and adding one is the four-place change in [AGENTS.md](AGENTS.md).
- `skill-record` cannot judge content quality, nor whether the trigger wording in a description is any good: skills are read by an agent and never executed by this kit.
- The cost of everything above is **maintaining the checks themselves**. Below a certain scale they cost more than they return, which is why the kit ships adoption tiers instead of one size.
