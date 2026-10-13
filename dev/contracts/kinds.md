# Contract: check kinds

English | [中文](kinds.zh.md)

## Interface

`tools/check-invariants.py` accepts exactly the check kinds listed in `SUPPORTED_KINDS`. A config naming any other kind is an environment error (exit 2), and so is a declared kind whose knobs are invalid. Every declared check must match at least one file: a check with no subject is rejected, never passed.

## Source of truth

`tools/check-invariants.py` owns the list, between its `# region: supported-kinds` and `# endregion: supported-kinds` markers. Adding a kind is a four-place change — runner, `SUPPORTED_KINDS`, a self-test case with its own negative probes, and the kind list in this document — and the paste below fails until all four land.

## Projection

The paste below is the complete `SUPPORTED_KINDS` tuple, fenced as `text mirror`. It is a projection, not a second definition: `dev/contracts/mirrors.json` registers it, and `python3 tools/check-invariants.py` compares it against the marked source region, ignoring trailing whitespace and blank edges and nothing else.

## Check

```sh
python3 tools/check-invariants.py             # fails on a stale paste
python3 tools/check-invariants.py --self-test # proves each kind can go red and green
```

## Drift policy

Update the paste in the same change that edits the tuple; update `dev/contracts/mirrors.json` when the block or either marker is renamed; delete this record when the check kinds are retired. A mirror whose doc leaves the check's `patterns` is reported, so re-scoping the check cannot silently orphan the paste.

## The registered paste

```text mirror
SUPPORTED_KINDS = ("tier-manifest", "budget", "required-sections", "source-mirror", "capability-registry", "criteria-traced", "forbidden-regex", "note-class", "link-target", "evidence-record", "skill-trigger", "publish-manifest", "sealed-manifest")
```
