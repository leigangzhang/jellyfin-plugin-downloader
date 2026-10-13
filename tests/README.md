# The kit's own tests

English | [中文](README.zh.md)

The suite proves the kit's own machinery: that every guard can fail, that the declarations match the corpus, and that the tools keep their contracts. [docs/testing.md](../docs/testing.md) owns the testing policy; [AGENTS.md](AGENTS.md) owns the rules for adding a test here.

Run it from the repository root, like every other command in this kit:

```sh
python3 -m unittest discover -s tests
```

## Directory structure

| Path | What it is |
|---|---|
| `__init__.py` | makes this a package, so the modules import each other by name |
| `harness.py` | what the modules share: the root, the loaders that read a tool by path, and the heading reader |
| `test_policies.py` | the policy owners: each standing document carries the shape its check gates, and the declarations agree with it |
| `test_records.py` | the record layer: class folders, publication, seals, and the surfaces a change claims |
| `test_pairing.py` | the pairing layer: one record per pair, recomputed rather than trusted, the two sides the same shape, and the switchers pointing across |
| `test_evidence.py` | the evidence runner: it must never mistake a sentence or a placeholder for a command |
| `test_tiers.py` | the tier switch: the switch, the tree, and the map's tier table must agree in both directions |
| `AGENTS.md` | the rules for adding a test here |
| `README.md` | this page |
