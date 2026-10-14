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
| `kit/` | the kit's own machinery: [policies](kit/test_policies.py), [records](kit/test_records.py), [pairing](kit/test_pairing.py), [evidence](kit/test_evidence.py), and the [tier switch](kit/test_tiers.py) |
| `backend_sources/` | the L1 source chain: index paging, keyword ladder, season detection, release keys, pool merge, external candidates |
| `backend_retired/` | retired paths, station config, site-URL classification, and the guards that read the plugin page's own source |
| `backend_probe/` | the probe chain: scoring, soft budget, offline dry run, integration contracts, and the cleanup guard |
| `fixtures/` | the trimmed real responses the offline cases replay |
| `AGENTS.md` | the rules for adding a test here |
| `README.md` | this page |
