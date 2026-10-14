#!/usr/bin/env python3
"""Write or verify this kit's bilingual-pair consistency records.

A pair is `X.md` (English), `X.zh.md` (Chinese), and `X.i18n.yaml` (the
consistency record). `--check` recomputes every record from the two documents
and reports a missing sibling, an absent language switcher, a fenced code block
that differs between the sides, or a section whose recorded hash no longer
matches its content; `--write` rewrites the records. Scope, exclusions, and the
anchors whose records must stay byte-identical to a host repository's renderer
come from `pairing` in `tools/workflow.json`, this kit's single configuration
file. The hash model is the one `docs/i18n/README.md` describes: per heading
section, a 16-hex SHA-256 of that side's top-level blocks, with fenced code
blocks left out.

The implementation lives in `tools/pairdocs/`; this file keeps the path, the
command line, and the import surface unchanged.
"""

from __future__ import annotations

import os
import sys

# The kit loads this file by path, from a process whose `sys.path` does not carry
# `tools/`, so the package beside it has to be made importable here.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pairdocs import *  # noqa: E402,F401,F403
from pairdocs import main  # noqa: E402,F401

if __name__ == "__main__":
    sys.exit(main())
