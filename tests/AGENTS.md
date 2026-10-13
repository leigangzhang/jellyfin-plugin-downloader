# AGENTS.md — tests

The suite proves the kit's own machinery: that every guard can fail, that the declarations match the corpus, and that the tools keep their contracts. [testing.md](../docs/testing.md) owns the policy; this file owns the layout.

**One module per subject.** [harness.py](harness.py) holds what the modules share — the root, the loaders, and the heading reader — and `test_policies`, `test_records`, `test_pairing`, `test_evidence`, and `test_tiers` each own one subject. A new invariant goes in the module that owns its subject; a new subject is a new module.

**A test here earns its place by failing.** Watch the case go red for the regression it pins before trusting it green. Flake is a defect, never noise: do not fix it with a longer timeout, a retry, a sleep, a serial suite, or a weaker assertion — retry only at a real external boundary ([policy](../docs/testing.md#prove-a-new-guard), [flake policy](../docs/testing.md#flake-policy)).

**The corpus tests run on the real tree, not on a fixture.** Rules that only hold for the repository as a whole — pairing, the tier table, the publication switch — are asserted against the working copy, because a fixture cannot prove the tree is in step.

**Load a tool, never import it across roots.** Every script in [tools/](../tools/README.md) has a hyphenated name and the engine is a package; [harness.py](harness.py) loads both, so no module needs path surgery of its own and every import here resolves for a reader and an editor.

**Run from the repository root**, like every other command in this kit: the configuration's paths are relative to it.
