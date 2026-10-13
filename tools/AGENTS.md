# AGENTS.md — tools

The five scripts are this kit's whole implementation. [README.md](README.md) is their reference and [workflow.json](workflow.json) is their only configuration.

**One configuration file.** Surfaces, checks, evidence commands, and the pairing scope all live in `workflow.json`; a second place declaring the same fact is drift.

**One module per subject.** `core` holds what every guard shares, each other module holds one subject — documents, records, publication, seals, criteria, capabilities, skills, tiers — and `registry` holds the kinds, the runners, and the self-test. A new guard goes in the module whose subject it belongs to; a new subject is a new module, never a longer one.

**A new check kind is a four-place change**: the module that owns its subject, its entry in `SUPPORTED_KINDS` and `RUNNERS` in [kitcheck/registry.py](kitcheck/registry.py), a `SELF_TEST_CASES` entry beside its runner with a positive and a negative probe, and the mirrored paste in [dev/contracts/kinds.md](../dev/contracts/kinds.md). A check no fixture can make red is noise, not a guard.

**A generator ships with its freshness gate.** Every generated artifact declares a `--check` command on its surface, and `run-evidence` really runs it; change the generator or its source and re-run it rather than patching the artifact, and never treat a regenerated page as the owner of the fact.

**Evidence is executed, not retyped.** A command's result has three states, and a manual entry is never counted as verified — [README.md](README.md) owns that semantics, and [.agents/skills/pick-evidence/SKILL.md](../.agents/skills/pick-evidence/SKILL.md) owns when to run it.

**Fail loud on misconfiguration.** A check whose patterns match no file is void, not passing; a declared command that cannot resolve is red before it is run.

**One concern per commit.** A change that spans tools carries only the reason it exists: a refactor and a behaviour change are separate commits, so a reader bisecting a break can point at one layer instead of a lump.
