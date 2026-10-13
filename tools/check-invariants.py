"""Executable conventions: every check here can fail, and --self-test proves it.

Checks are declared in tools/workflow.json. Twelve kinds are supported:

  budget                files matching `patterns` stay at or under `maxLines`, `maxWords`
                        (whitespace-separated, as `wc -w` counts them), or both
  required-sections     files matching `patterns` contain every section in `sections`,
                        and each section body has at least `minBodyLines` non-blank lines;
                        an entry may list the accepted spellings of one section, so a
                        page that translates its headings declares its own wording
  forbidden-regex       files matching `patterns` contain no `regex` match
  note-class            files matching `patterns` are records at
                        .agents/notes/<lifecycle>/<class>/<file>; the class is one of `classes`,
                        the lifecycle is one of `lifecycles`, and the file's `Class:` line
                        agrees with its folder, so a record cannot be filed under the
                        wrong class and drift away from the taxonomy
  source-mirror         fenced blocks registered in `manifest` equal the source region
                        between a `begin` and an `end` marker line; trailing whitespace
                        and blank edges are ignored, nothing else is
  link-target           files matching `patterns` carry no relative link that names no
                        file; a link into a stage the tier switch declares absent is
                        exempt, and the scanner is the one tools/pair-docs.py uses
  evidence-record       files matching `patterns` are records written by
                        tools/run-evidence.py: each carries the shape a reader needs, and
                        its `Verdict:` counts agree with the entries below it
  capability-registry   capabilities registered in `manifest` are complete, and their
                        keys equal the `capability: <key>` markers discovered in source
  criteria-traced       every bullet in the listed `sections` carries an id and cites a
                        check id or surface name read from `declaredIn`, so no criterion
                        passes without an owner that is configured to go red
  skill-trigger         a skill's front-matter `description` opens with when the skill
                        loads, not with a summary of the skill's own contents
  publish-manifest      every file matching `patterns` is declared exactly once as
                        `public` or `internal` in `manifest`; each `public` entry names
                        one file that exists, and every `internal` entry states why
  sealed-manifest       every file matching `patterns` except the manifest itself is
                        registered exactly once in `manifest` with the SHA-256 of its
                        text, its archive date, and a reason; the file must still hash to
                        that value and must declare `Archived: <that date>`

Patterns use fnmatch semantics, in which `*` also matches `/`, so "*.md" covers every
Markdown file at any depth and "AGENTS.md" covers only the root file.

A `source-mirror` manifest is a JSON object with a non-empty `mirrors` list. Each entry
names `doc`, `fence`, `source`, `begin`, and `end`; blocks and entries are one-to-one, a
duplicate `(doc, fence)` is a violation, and a `doc` outside the check's `patterns` is
reported because it would never be scanned. The check exists because a pasted contract
nobody re-checks silently becomes a second, wrong fact.

A `capability-registry` manifest is a JSON object with a non-empty `capabilities` list.
Each entry names `key`, `kind`, `definition`, and `note`; a `seam` also needs at least
one `providers` and one `consumers` path, and every path must exist. With `discover`
configured, the registered keys and the keys declared under `discover.patterns` must be
exactly equal in both directions: a declaration is a line whose first non-space text is
`discover.marker` followed by the key, so the marker string normally carries the
comment syntax (`# capability:`), and prose that merely mentions the marker does not
count. The check exists because a capability that nobody classifies, or a
classification whose subject is gone, is invisible either way.

A `publish-manifest` manifest is a JSON object with a non-empty `public` list and an
`internal` list. Every file matching the check's `patterns` must match exactly one
declared entry: a file nobody declares is a silent omission rather than an unpublished
document, and a file declared twice is an ambiguity. A `public` entry names one file and
may not contain glob magic, because publication is a per-file promise; it must also
match a file, since a promise with nothing behind it is a broken link. An `internal`
entry may name a home and may match none, because an empty shelf is not a promise, but
it must say why the file stays. The check exists because "the file exists" and "the file
ships" are different facts, and only the manifest can tell them apart.

A `sealed-manifest` manifest is a JSON object with a `sealed` list, which may be empty:
an archive is append-only, so it starts with nothing in it, and the guard is not vacuous
because every file matching the check's `patterns` — except the manifest itself, which
cannot seal itself — must be registered. Each entry names `path`, `sha256`, `archived`,
and `reason`; `sha256` is the digest of the file's UTF-8 text with universal newlines, so
the seal freezes content rather than the line endings of the machine that wrote it. Every
entry must fall inside the check's `patterns`, name a file that exists, hash to the
recorded value, and agree with an `Archived: <date>` line in the file itself. The check
exists because an archived record is kept for a reason, and a record that can still be
edited is a record that will contradict the reason it was kept for.

A `criteria-traced` check turns every bullet under its `sections` into a claim with an
owner. Each bullet carries an id matching `idPattern` (default `[A1]`, `[AC-2]`) and
cites at least one name in backticks that is declared in `declaredIn` as a `checks[].id`
or a `surfaces[].name`. Backticked text that is not a bare lowercase name — a command, a
path, a sentence — is free text and is not resolved, because no check can run it. A
section with no bullets is rejected too: a heading that carries nothing cannot be
traced. The check exists because a criterion nobody owns is a wish, and the owner it
names is exactly the thing that will go red when the criterion breaks.

Exit codes:
  0  every check passed, or --self-test passed
  1  at least one violation, or a self-test failed
  2  usage or environment error (bad config, unsupported check kind, invalid regex)
"""

from __future__ import annotations

import sys

import importlib.util as _importlib_util
from pathlib import Path as _Path

_PACKAGE = _Path(__file__).resolve().parent / "kitcheck"
_SPEC = _importlib_util.spec_from_file_location("kitcheck", _PACKAGE / "__init__.py",
                                               submodule_search_locations=[str(_PACKAGE)])
if _SPEC is None or _SPEC.loader is None:                      # pragma: no cover - packaging error
    raise SystemExit("tools/kitcheck is missing")
_ENGINE = _importlib_util.module_from_spec(_SPEC)
sys.modules["kitcheck"] = _ENGINE
_SPEC.loader.exec_module(_ENGINE)

if __name__ == "__main__":
    raise SystemExit(_ENGINE.main())
