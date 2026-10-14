# Proposal: Console layout changes are judged by rendering and measuring, not by source strings

English | [中文](2026-10-14-console-layout-judgment.zh.md)

Status: proposed
Date: 2026-10-14
Class: testing

## Problem

Someone changing the look of this plugin's pages must stop the running service, rebuild and reinstall before the result can be seen, and then measure elements one by one on the live page to know whether anything moved. So nobody dares to unify what the two pages look like.

One session of layout work on 2026-10-14 gives the numbers (`5418504`, `7cef34c`, `edbbe9f`, `cb083f3`, `f413ecf`, `0432907`):

- Every revision cost a `./build.sh` + `./install.sh` cycle, and `install.sh` stops Jellyfin before overwriting the plugin: about ten seconds of downtime per attempt, eight attempts in that session.
- "Not broken" meant source-string assertions in [test_backend_retired_config_page.py](../../../../tests/backend_retired/test_backend_retired_config_page.py) — `assertIn("flex: 0 0 var(--jdl-actions-width)")`, `assertIn("运行中")`, `assertNotIn("status.pid")` among the file's 53. They stayed green while the page was wrong: the equal-width requirement was recorded in `cb083f3` as covering the media-directory field (measured 803.5 px) as well as the site fields (627.5 px), and only the reviewer measuring coordinates found it.
- Geometry was taken by hand through the browser's own tooling, six to ten elements per revision: `#jdl-media` x=254.875 w=627.515625, `[data-action="up"]` x=890.390625 in both rows, `#jdl-port-label` height 30.125.
- Computable properties were computed by hand as well: six WCAG contrast ratios, two of which failed (`#2e9e4f` 3.43:1, `#d79b00` 2.45:1 against white).
- A soft page reload serves the cached old page. One round of measurements was taken against it and thrown away, and the hard reload only showed up because a coordinate refused to change.

The cost is not the rebuild. It is that no claim about a page can be checked without a person looking at it, so a layout change cannot be reviewed, and a redesign cannot be cut into pieces someone else can land.

## Proposal

Give both console pages one judgment surface and make it the thing that decides whether a layout change is correct.

1. A render step loads `Configuration/configPage.html` with the dashboard's own stylesheet (the `main.jellyfin.*.css` the theme ships) in a headless Chromium, with no Jellyfin running and no plugin installed.
2. A measurement step reads each declared element's box and computed style, then asserts the relations the page has to keep: equal input widths inside a list, the three row buttons in one column across rows, the port row's parts on one line, a section label the same size as the other section titles, and a status-badge palette that passes WCAG AA.
3. Expected values live in one file next to the lane, and every run prints what it measured, so updating an expectation is a deliberate edit rather than a guess at a red.
4. The command runs while Jellyfin is up and touches nothing under the installed plugin directory, so judging a layout change stops needing `./install.sh` — and a soft-reload trap disappears with it, because the lane reads the files rather than a served page.
5. Which files the lane renders is derived from the `source` surface declaration, so a sixth script shard or a new stylesheet cannot stay outside it unnoticed.

Constraints the lane has to honour: stdlib Python only, no new package dependency and no Node toolchain (this repository's tests are `python3 -m unittest discover -s tests`); when no Chromium is present the lane fails with a named reason instead of reporting success.

New: a test module and its expectation file under `tests/` (the `tests` surface), the render helper next to it, and one declared `checks[]` entry in [workflow.json](../../../../tools/workflow.json) so `check-invariants` reports the lane by name.

Open question the first delivery answers: whether driving a locally installed Chromium over its devtools protocol from stdlib Python is small enough to keep, or whether the render step needs a declared dev dependency. The lane is worth building either way; the dependency call is made with the diff in hand.

## Alternatives considered

**Keep the source-string assertions and hand-measured coordinates.** They are cheap, they run in the existing command, and they do pin textual regressions (a `status.pid` that comes back). They lost because they assert the shape of the source rather than the result: five of them were green while the equal-width claim was false, and every geometry relation the last six revisions touched is invisible to them.

**Measure on the running instance, in the real dashboard.** Highest fidelity, no stand-in stylesheet, and the browser is already there. It lost because the evidence would then depend on a live service and an admin session: producing it means stopping and reinstalling the plugin — the cost this proposal exists to remove — and nothing in it could ever run as part of the repository's own acceptance command.

**Compare screenshots instead of asserting geometry.** Visual differencing catches what geometry cannot (spacing feel, wrapping, overlap), and screenshots were genuinely taken and read during the session. It lost as the first slice because every difference is a human judgement, so the lane could not fail on its own; it is the natural third stage once the relations exist.

## Acceptance criteria

- [A1] `source` — the lane covers every frontend file: `Configuration/configPage.html`, `Web/inject_01_core.js` through `Web/inject_05_render.js`, `Web/style.css`. The list is derived from the surface declaration, so covering a new shard is not optional and a missing one fails rather than passes silently.
- [A2] `tests` — reverting the row's button area to `flex: 0 0 auto` (the defect fixed in `cb083f3`) fails a criterion; the run that first showed red is recorded here, because a guard nobody has seen fail is a guess.
- [A3] `tests` — the lane passes while Jellyfin is running, needs neither `./build.sh` nor `./install.sh`, and leaves `git status` clean when run twice in a row.
- [A4] `source` — the lane renders the source files, so editing a style and re-running reports the new measurement without a rebuild; with no renderer available it fails naming that cause instead of succeeding.
- [A5] `docs` — the command is named where an agent looks for this repository's acceptance commands, and the claim that a page change can only be confirmed by stopping the service is gone from [README.md](../../../../README.md).

## Risks

- A stand-in render is not the dashboard. The theme stylesheet comes from the installed dashboard, but the surrounding DOM and its resets differ, so a relation can hold in the lane and break on the real page. The first run reconciles against the live baseline measured on 2026-10-14 — inputs x=254.875 w=627.515625, both rows' up-button x=890.390625, `#jdl-port-label` height 30.125 — and that reconciliation stays runnable.
- Expectations become a second home for layout facts as long as the page keeps its own copies. Accepted for now, because every run prints the measured values and updating them is explicit. This is the deferred "one home for layout facts" slice: when the same fact drifts between two homes a second time — as on 2026-10-14, when [README.md](../../../../README.md) described a Python-install control the page no longer had — that slice becomes the next proposal.
- The renderer is the fragile part. Stdlib-python-over-devtools-protocol is more code than one library call, and where no Chromium exists the lane is void rather than passing. If it proves brittle, the fallback is a declared dev dependency — a decision this proposal deliberately does not make yet.
- The redesign is not in this proposal. Once the lane can go red reliably, a separate proposal covers unifying the two pages' appearance; deciding it earlier means writing criteria nothing can check, which is why this note exists first.
- Appearance stays unverified at the pixel level. This proposal pins relations and computable properties, not looks; judging looks remains a human step, and the lane's screenshots only make that step cheaper.

## Delivery stages

1. Config page: render plus geometry and contrast assertions, file coverage derived from the surface, and the red witness for A2.
2. Injected panel: the same lane against `Web/inject_0*.js` with a stubbed host DOM, reusing the expectation file — the panel only exists inside Jellyfin's item page, so this stage is what makes stage 1's relations hold there too.
3. Screenshots as evidence: the lane emits the shots a reviewer already reads, so the review reads a named artifact instead of re-driving a browser.
