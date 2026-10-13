# The eleven-station lifecycle

English | [中文](README.zh.md)

> The smallest working implementation distilled from DeepSeek Harness. One sentence runs through the whole thing:
> **every station produces something decidable. What you cannot decide may not enter the next station.**

This page is also the landing page for `dev/`: each station's artifact lives in the directory its row names below, while the rules each station follows are the standing documents under `docs/`.
>
> The "Gates" column of each station names check ids that really exist in [tools/workflow.json](../tools/workflow.json) — they are not a metaphor.

## The stations at a glance

| # | Station | Artifact (home) | Gates | Who decides |
|---|---|---|---|---|
| 1 | Intent | The `Class:` field in a record header | manual | The requester |
| 2 | Proposal | `.agents/notes/proposed/<class>/<date>-<slug>.md` | `decision-proposed`, `criteria-traced` | The proposer |
| 3 | Decision | `.agents/notes/implemented/<class>/<date>-<slug>.md` or `.agents/notes/rejected/<class>/<date>-<slug>.md`; the capability registry `dev/capabilities/registry.json` | `decision-implemented`, `no-proposal-era-headings`, `decision-rejected`, `capability-registry` | Proposer + review |
| 4 | Contract | `dev/contracts/<slug>.md` plus `dev/contracts/mirrors.json` (or a source interface file plus a link) | `contract-record`, `contract-mirror` | The interface owner |
| 5 | Implementation | The `## Consequences` section of the record, or a task entry | `criteria-traced`, `change-scope --strict` | The implementer |
| 6 | Verification | Tests + golden files, and the delivery's one record under `dev/evidence/`; the strategy lives in `testing.md` | `testing-policy`, `no-time-based-test-sync`, `--self-test`, the empty-corpus rule, `criteria-traced`, `run-evidence` | The implementer |
| 7 | Review | `dev/review/<date>-<slug>.md` | `review-record` | **A person** |
| 8 | Integration | The commit history + the `change-scope` report + a pre-landing preflight | `change-scope --strict` | A person |
| 9 | Release | `dev/release/<version>.md` + an immutable artifact | `release-record` | A person |
| 10 | Evolution | `dev/upgrade-guide/<version>-<surface>.md`; the rules live in [evolution.md](../docs/evolution.md) | `dev/upgrade-guide`, `upgrade-guide-budget`, `evolution-policy` | A person |
| 11 | Retirement | `.agents/notes/archived/` plus [manifest.json](../.agents/notes/archived/manifest.json) (seal + digest + date) | `archive-seal` | A person |
| ⚡ | Incident (cross-cutting) | `dev/postmortem/<NNNN>-<slug>.md` | `postmortem-record` | The incident handler |
| 📄 | Documentation (cross-cutting) | [documentation.md](../docs/documentation.md) + [docs/](README.md) + [i18n.md](../docs/i18n.md) + [glossary.md](../docs/glossary.md) + [plain-language.md](../docs/plain-language.md) | `docs-policy`, `docs-budget`, `publish-manifest`, `i18n-policy`, `i18n-budget`, `generated-docs` (freshness through `run-evidence`) | A person |

## Adoption tiers

You do not need all eleven stations at once. Pick by where your project is now:

| Tier | Stations to install | Fits |
|---|---|---|
| **Minimum** | 1 · 2 · 3 · 4 · 6 · 7, plus documentation, tests, and capabilities | One person iterating fast, with no users yet |
| **Delivery** | The above + 9 · 10 (including [evolution.md](../docs/evolution.md) and `dev/upgrade-guide/`) | A public interface, and other people using it |
| **Long-lived** | Everything + 5 · 8 · 11 (including the `.agents/notes/archived/` seal), rejected records, and incidents (`.agents/notes/rejected/`) | Several rounds of evolution, several people, and a long life |

For a station you do not install, **its checks turn red for matching no file** (see station 6) — deliberately, because a hollow check is worse than no check. So declare it `none` in [tools/tiers.json](../tools/tiers.json): the switch turns the station into a declared absence, the checks that own nothing but its home stop running, and the empty-corpus rule stops applying to them. That is the whole instruction — the eight mappings this paragraph used to list are now the switch's job. Remove the directory as well, because the switch states intent and the tree states fact: a stage the switch calls absent while its files are still there is red, and so is a stage it calls installed whose home is empty.

---

## 01 · Intent

**What it solves**: turning "what is wanted" into something **classifiable**, so the later rules can act per class.

**Entry** → someone raises a request or a problem.
**Exit** → it is filed under a `Class` and written into a record (or an issue).

**Artifact**: the `Class:` field in the record header. The closed set is `feature` / `bug-fix` / `simplification` / `architecture` / `process` / `testing`.

**Gate**: manual — classification is a judgement call. It cannot stop laziness, only discipline can.

**Anti-pattern**: routing every request through the same heavy process. **A bug fix does not need a Proposal**; giving it the full process only teaches people to route around it.

---

## 02 · Proposal

**What it solves**: writing down **what you are rejecting**, and **what done looks like**, before any code exists.

**Entry** → a non-trivial change starts.
**Exit** → `.agents/notes/proposed/<class>/<date>-<slug>.md` exists, and: `Alternatives considered` is **not empty**; and **every** entry under `## Acceptance criteria` carries a stable id and names, inside backticks, **the check that will go red** for it (a check id or surface name already declared in `tools/workflow.json`).

**Artifact**: [.agents/notes/proposed/TEMPLATE.md](../.agents/notes/proposed/TEMPLATE.md)

**Gates**: `decision-proposed` (any missing section is red) + `criteria-traced` (a missing id, or one naming a check that does not exist → red)

**Discipline**: a criterion has exactly four **landing places** — an assertion, a command, a snapshot, or an external observation. What cannot land in any of them gets deleted, or admitted as taste rather than acceptance.

**Anti-pattern**: writing the Proposal as a list of implementation steps — that is task breakdown. A Proposal answers only "why do this" and "what does done look like".

---

## 03 · Decision

**What it solves**: separating "a decision that landed" from "a proposal still under discussion" physically, and keeping **one fact version per decision**.

**Entry** → the proposal is implemented.
**Exit** → the file moves into `.agents/notes/implemented/<class>/` and is **rewritten in the present tense**: `## Proposal` → `## Decision`; `## Acceptance criteria` + `## Risks` fold into `## Consequences`, and the part that was verified is rewritten as `## Testing`, **keeping the original ids**.

**Artifact**: [.agents/notes/implemented/TEMPLATE.md](../.agents/notes/implemented/TEMPLATE.md)

**Gates**: `decision-implemented` requires `## Decision`, `## Consequences`, and `## Testing`, so **a record that was not rewritten is red**; `no-proposal-era-headings` blocks the other direction: a leftover `## Proposal` / `## Plan` / `## Migration plan` / `## Acceptance criteria` after the move is red too. Both directions are blocked because requiring only "the new skeleton is present" would let one record carry two skeletons at once.

**Discipline**: the id is the **only carrier** an acceptance criterion has across the move. Lose the id and you lose "who proves this criterion".

**Discipline**: a fact may be corrected in place (a path or a default follows the code); **a decision may not**. Overturning a decision means writing a new record and cross-linking it.

**Anti-pattern**: appending "Update: it was later changed to…" to the old record. The next reader takes the history for current fact.

### 03.1 A special form of decision: does a capability take shape (seam)

Some decisions are not "should this function change" but "does this thing earn being a **replaceable capability**". This layer has its own discipline and artifact because its mistakes cost the most: an abstraction with no consumer gets treated as infrastructure by whoever comes next.

- **Three roles**: the **Definition** (owns the interface and the vocabulary), the **Provider** (implements it), and the **Consumer** (uses only the Definition, never a provider's type). **One role alone is not a capability**: a Definition with no Provider is a wish, a Provider with no Consumer is a liability, and a Consumer wired to one Provider is not replaceable.
- **No preemptive splitting**: with one imaginable Provider and one Consumer, the three roles stay in one file or package until a second appears. "We may need it later" is not a reason.
- **A current consumer is mandatory**: an abstraction, a switch, or a compatibility path binds to a consumer that is **in use right now**; without one, do not add it yet.
- **Not splitting also gets a reason**: an entry whose `kind` is not `seam` must state in its `note` why it is not one.

**Artifact**: the registry entry defined by [capabilities/TEMPLATE.md](capabilities/TEMPLATE.md), written into `dev/capabilities/registry.json`.

**Gates**: `capability-record` (all five template sections) + **`capability-registry`** (registry and reality **aligned in both directions**):
- every capability leaves a leading declaration in source (default `# capability: <key>`);
- declared but unregistered → unclassified; registered but undeclared → a stale entry;
- `kind: seam` must give a Definition, at least one Provider, and at least one Consumer, and every path must really exist;
- an entry that is not a seam must state its reason.

It is the same pattern as the contract layer: **existence is discovered from source, classification is written by a person, completeness is enforced by the gate.**

**Anti-pattern**: splitting the three roles into three packages early "so it will be easy to extend", or never updating the registry after writing it, letting a stale capability map keep posing as the current architecture.

---

## 04 · Contract

**What it solves**: making it **impossible for a document to expire quietly**.

**Entry** → an interface, a type, a config key, or a protocol owes something to another part.
**Exit** → one of two forms, and it must be decidable:
- **pointer**: the interface lives in source or a schema file (`.ts` / a JSON schema / OpenAPI), and the document carries a link and a one-line summary;
- **mirror**: the exact declaration is pasted into the document and registered in `dev/contracts/mirrors.json`, and `contract-mirror` compares it byte for byte with the source region between its `begin`/`end` markers.

**Artifact**: the five-section record defined by [contracts/TEMPLATE.md](contracts/TEMPLATE.md) (Interface / Source of truth / Projection / Check / Drift policy) plus the registry entry in `dev/contracts/mirrors.json`.

**Gates**: `contract-record` (any of the five sections missing is red) + `contract-mirror` (byte comparison, one-to-one both ways). Whether the paste is worth having, and whether it is written well, stays manual.

**Discipline**: **registration and paste update in the same change.** Source moves and the paste does not follow → red; registered but the block is gone → red; a block nobody registered → red; a mirror block moved out of the scanned patterns → red.

**Anti-pattern**: pasting a copy of an interface into a document and then forgetting to sync it. **Restatement is drift** — two facts diverge at some point, inevitably. Either do not paste (pointer), or paste and register and compare (mirror).

*(DeepSeek Harness is one implementation of this: `ts type-equiv` pulls declarations and JSDoc out of source with a parser and compares them byte for byte with ` ```ts type-equiv ` blocks in the document; 471 primary blocks, 471 derived Chinese blocks, and 101 source files were all registered at the time of measuring.)*

---

## 05 · Implementation (the definition of "done")

**What it solves**: defining "done".

**Entry** → the contract is ready.
**Exit** → **definition / implementation / consumer** all present, and the consumer is **in use right now**. Missing any one is recorded as "not done", not as "roughly usable".

**Decision table** (walk it line by line against this change):

| What you have | The verdict |
|---|---|
| An interface or abstraction | **Not done**: there must be at least a real Provider |
| An interface + one implementation, called only by its author | **Not done**: self-consumption is not a consumer |
| All three roles, with the Consumer wired to one implementation | **Not done**: a Consumer may depend only on the Definition |
| All three roles, but only one imaginable Provider or Consumer | **Do not split**: "we may need it later" is not a reason |
| All three roles, and the roles change at different rates for different reasons | **A capability that took shape** |

**Artifact**: the `## Consequences` section of the record, or one line in a task entry naming who consumes it.

**Gates**: `criteria-traced` (every acceptance criterion carries an id and names its owning check) + `change-scope --strict` (any file with no declared owner turns it red). "Is there a current consumer" is a judgement call the gate cannot make — so it must be written into the task entry for review to read.

**Anti-pattern**: "it is roughly usable". A capability with no consumer is not half-finished, it is a **liability**: it reads as "done", and the next round depends on it as infrastructure.

---

## 06 · Verification

**What it solves**: making "it ran" and "it ran through the real entry path" the same statement.

**Entry** → an implementation exists.
**Exit** → every acceptance criterion's id has an owner that **can go red**; user- or model-visible output has a **replayable expected file**; someone has **seen a new guard go red**; the evidence that ran is reported by command and result, and the delivery leaves its **one** record under `dev/evidence/` when it lands (command + three-state result: passed / failed / unknown); and a manual entry was either really done or is explicitly marked unverified.

**Artifact**: tests + golden files, plus the delivery's one record at `dev/evidence/<slug>-<merge-base>.md`. **The test strategy lives in [testing.md](../docs/testing.md)** (its seven sections: Tiers / Evidence per change / Determinism / Blocking and observational lanes / Prove a new guard / Flake policy); LIFECYCLE only owns this station's entry and exit.

**Gates**:
- `testing-policy`: `testing.md` has all seven sections, non-empty — the strategy has one home, and that home has a shape;
- `no-time-based-test-sync`: tests may not contain a fixed wait (`setTimeout(` / `waitForTimeout(` / `sleep(`) — a wait must hang off a state;
- `criteria-traced`: every criterion carries an id and names a declared check or surface — turning "which one goes red when I break it" into a field the document must carry;
- `--self-test` + **the empty-corpus rule**: every check needs a positive and a negative sample; a check matching no file is **void outright** (a check with nothing to check is a shell);
- the `tests` surface: when you change a test file, `run-evidence` really runs that command and writes the three-state result into the record.

**Pick the tier before writing the test** (details in [testing.md](../docs/testing.md#tiers)):

| Tier | What it proves | What it misses |
|---|---|---|
| Unit | Edges, error paths, event order, races | Real composition, real entry paths |
| Real entry | Start once through real composition or a real artifact, and assert the **world** (not a self-report) | Value for money on edge cases |
| Replayable expectation | Record the visible output, replay and compare; **a person reads every diff** as a behaviour change | Behaviour with no visible output |
| Coverage | Every line has an owner | "The line ran" is not "the behaviour is right" |

**Discipline**:

- **A new guard must be able to go red** — break it once, watch it go red, put it back. What you have never seen red is not a guard.
- **Distrust the green signal by default** — ask two things of every "pass": who says so, and am I reading external state or its own self-report? Before delivery, walk the eight questions in [.agents/skills/apply-distrust/SKILL.md](../.agents/skills/apply-distrust/SKILL.md); spend distrust only across a boundary and where a signal can lie.
- **Determinism is part of the test**: allocate resources atomically, synchronize on state (never sleep), wrap process-global state, and give each lane a timeout budget.
- **Flake is not noise**: a longer timeout, a retry, a fully serial suite, a weaker assertion, or an added sleep is not a fix; only a transient failure at a real external provider may be retried at that boundary.
- **Mark each lane blocking or observational**: a platform-specific known instability **is downgraded to observational**, never deleted and never weakened; an observational item must still report, and you must write how many consecutive green runs promote it back to blocking, or it rots into a list nobody reads. See [testing.md](../docs/testing.md#blocking-and-observational-lanes).

**Anti-pattern**: testing only the happy path; writing an assertion that is always true "to raise coverage"; retrying an instability away as if it were noise.

---

## 07 · Review

**What it solves**: putting an **independent evidence requirement** between the author and the verdict.

**Entry** → the change can run.
**Exit** → `dev/review/<date>-<slug>.md` exists; `## Evidence` holds **external observations** (commands, output, file state), not "I read through it"; and every finding has a recorded destination under `## Follow-up`.

**Artifact**: [review/TEMPLATE.md](review/TEMPLATE.md)

**Gate**: `review-record`

**Discipline**: **a finding must get a destination**, one of three — **become a check** (with its negative control in the same change) / **become a rule** (the kind a check cannot decide, written into the matching file) / **be deliberately dropped** with the reason (one-off, not reproducible, costs more to maintain than it returns). "Deliberately dropped" is allowed; **not recording it is the problem** — a finding with no destination is simply found again.

**Anti-pattern**: letting the agent that wrote the code declare "reviewed, no issues". **A review from the same source is no review** — it just states the same blind spot twice.

---

## 08 · Integration

**What it solves**: landing several changes in dependency order, verifiably, with **evidence from the moment of landing**.

**Entry** → review passed and the change can run.
**Exit** → changes land in dependency order; **the evidence for every affected surface is re-run after landing**; and every "resolved / passing" claim has a **post-landing** confirmation.

**Artifact**: the commit history itself + the `change-scope` report + a **pre-landing preflight** (re-resolve the exact base/head, re-read the diff).

**Gate**: `change-scope --strict` + re-running station 6's verification. `--strict` turns "a new file with no owner" into a failure instead of an oversight.

**Discipline**:

1. **Preflight before landing.** Re-resolve base and head before merging; trust neither the branch name nor the previous report. Every change is judged **ready on its own**: a green tip does not mean the layers below it are green, and an aggregate green is no evidence for any single surface.
2. **Any history rewrite voids the old evidence.** After a rebase / amend / squash, every earlier "passing, resolved, approved" is void. Re-fetch the exact head, re-read the diff, re-run the affected surfaces' evidence, and only then talk about ready.
3. **Choose the rewrite strategy explicitly.** merge-forward (keep the merge checkpoint, rewrite no history) and rebase (rewrite history) are both allowed, but choose deliberately. A rewritten push may only carry lease protection (`--force-with-lease=<branch>:<observed-oid>`), and aborts the moment the remote moved; a bare `--force` is forbidden.
4. **Land one bisectable step at a time.** When something breaks you can point at the layer instead of reverting a lump.
5. **Teardown is its own last step.** Before deleting a branch, a temporary artifact, or an exclusive resource, confirm nothing else still depends on it; if something does, stop and resolve the dependency first.

**Anti-pattern**: merging one big lump (nothing is bisectable when it breaks); treating old results as evidence after rewriting history; deleting a branch or artifact without checking dependencies; taking "the aggregate is green" as proof that one layer is ready.

---

## 09 · Release

**What it solves**: turning "it runs on my machine" into "**it runs outside the repository, from the same bytes that were published**".

**Entry** → integration is done and the change sits on an exact revision.
**Exit** → `dev/release/<version>.md` exists, and:

- it names the **exact release revision and immutable artifact** (`## Revision`);
- `## Evidence` lists, **in execution order**: **rehearsal** (build without credentials → clean install outside the repository) → **publication** (an explicit human action). Interrupting a run leaves a readable prefix rather than an unordered pile of results.

**Artifact**: [release/TEMPLATE.md](release/TEMPLATE.md) + one immutable artifact (a tarball / a build directory / an image).

**Gate**: `release-record` (all five of `Revision` / `Scope` / `Evidence` / `Breaking changes` / `Rollback`, non-empty).

**Discipline**:

1. **Rehearsal and publication are separate.** A publish-equivalent build and an outside install **need no release credentials**, so every change can rehearse one; the real publication is an **explicit human step** that must not disguise itself as an automated check, nor happen automatically because something went green.
2. **The release boundary is the immutable artifact.** Produce an immutable artifact, verify it, publish it. **The publish step rebuilds nothing** — it uploads exactly the bytes rehearsal verified, because a build at publish time can ship content nobody verified.
3. **A clean install clears the host environment.** Changing directory is not enough. Use a throwaway `HOME` / config directory and a plain runtime, and explicitly drop host-leaking variables such as `NODE_OPTIONS`, `NODE_PATH`, language paths, and global git / npm hooks; otherwise "clean install" is an ordinary install in a different directory.
4. **Publication is idempotent.** Check the published state and digest first: **publish only when missing; skip when present with the same digest** (a re-run is safe); **fail when the digest differs** — the same version covering different content is a defect, not a retry.
5. **Record the exact revision.** The release record names the tag / revision and the artifact digest. When content changes, move to a new revision; never overwrite the same version.

**Anti-pattern**: running once from a path inside the repository and calling it published (in-repo success is **simultaneously** masked by `node_modules`, local caches, uncommitted files, and leftover build directories — only a clean install outside the repository rules out all of them at once); rebuilding at publish time (you no longer ship the verified bytes); overwriting one version with different content; treating "the rehearsal passed" as "published".

*(DeepSeek Harness's counterpart: `release:verify` asserts that verification runs from the family tag; `release:pack` packs a commit into an immutable tarball without credentials; `release:verify-packed-install` installs those into a throwaway consumer directory outside the repository, driven by plain Node; `release:publish` uploads only the bytes `pack` produced and decides "publish / skip / fail" from the registry's published digest. The publish workflow is triggered manually by `workflow_dispatch` and never appears as a PR check.)*

---

## 10 · Evolution

**What it solves**: a breaking change must leave behind **how a reader migrates**.

**Entry** → the change breaks something **already in use** (an interface, a config key, a data format, a command line).
**Exit** → `dev/upgrade-guide/<version>-<surface>.md` contains `## Change` and `## Migration`, and Migration's **last step is how to confirm the migration worked**.

**Artifact**: [upgrade-guide/TEMPLATE.md](upgrade-guide/TEMPLATE.md)

**Gates**: `dev/upgrade-guide` (both sections present) + `upgrade-guide-budget` (a line ceiling) + `evolution-policy` (the rule home has a shape)

**The rules live in [evolution.md](../docs/evolution.md)**: what changed → which record to write (the selector); the three version states recorded separately (the writer / the confirmed baseline / what was published); and a consumed artifact being append-only. This file owns only this station's entry and exit.

**Anti-pattern**: writing it as a CHANGELOG (the reader only cares what *they* must change); documenting several breaks in one version with a single guide — one guide owns exactly one surface.

---

## 11 · Retirement

**What it solves**: making the old thing **really disappear**.

**Entry** → an artifact loses its value, or starts misleading readers.
**Exit** → delete it, or move it into `.agents/notes/archived/<class>/`, add `Archived: <yyyy-mm-dd>` to its header, register it in [manifest.json](../.agents/notes/archived/manifest.json), and **never edit it again**.

**Artifact**: the `.agents/notes/archived/` directory + [manifest.json](../.agents/notes/archived/manifest.json) (seal + digest + archive date).

**Gate**: `archive-seal` — an unregistered drop, a file edited after sealing, and a date that disagrees with the registry are all red.

**The rules live in [evolution.md](../docs/evolution.md)**: the standard for delete / merge / keep-as-rejected / seal, and what to do when sealed content needs to change.

**Anti-pattern**: keeping everything. An old document does not sit quietly — it shows up as "fact" in the next reader's context.

---

## ⚡ · Incident (cross-cutting)

An incident belongs to no single station; it is **an input to all of them**.

**Entry** → a bug reached a place it should never have reached (a real user, merged code, a published version).
**Exit** → `dev/postmortem/<NNNN>-<slug>.md`, with `## Negative control` proving the new guard really does go red for this incident.

**Artifact**: [postmortem/TEMPLATE.md](postmortem/TEMPLATE.md)

**Gate**: `postmortem-record`

The test for writing one (all three together): the mechanism is **not obvious**, the cause is **systemic** (a gap in tests, tooling, or convention rather than a slip), and **rediscovering it is expensive**.

---

## 📄 · Documentation (cross-cutting)

Documentation is not a twelfth station; it is every station's output surface: a proposal becomes a decision record, an interface becomes a contract, verification becomes evidence, a breaking change becomes an upgrade guide. The rules themselves live in [documentation.md](../docs/documentation.md); this section covers only how it cuts across.

**Entry** → a fact now owes something to someone else (a reader, a plugin author, someone upgrading, or the next round's agent).
**Exit** → it lands in **exactly one home** and in one of three forms, with a checkable criterion. **A file existing is not a file shipping**: whether a consumer can take it is decided explicitly by [docs/publish.json](../docs/publish.json).

| Form | When to use it | The gate that holds it |
|---|---|---|
| **Link** (default) | The fact lives in source, config, or a generator | The `prose` surface; link checking is a placeholder evidence entry today — swap in your own |
| **Mirror** | A declaration must be shown verbatim | `contract-mirror`: byte comparison + registration and paste one-to-one both ways |
| **Generate** | A whole page must be exported exhaustively from one source | The `--check` on the `generated-docs` surface, executed by `run-evidence` |

**Gates**:
- `docs-policy`: the standard has one home, and that home has all seven sections (kinds / ownership / tutorial and reference / the three forms / budgets / publication / the slop checklist);
- `docs-budget`, `contract-kinds-budget`, `i18n-budget`: a standing document is a budget, measured in lines or words;
- `publish-manifest`: every Markdown file is **exactly** one of `public` or `internal` — a file nobody classifies is a silent omission, not "unpublished"; a `public` entry pointing at nothing is red too;
- `generated-docs`: a generated page is read-only — change the generator, re-run it, and let `run-evidence` prove it is not stale.

**Discipline**:
- **One home per fact**: when a sentence appears twice, delete one and link the other; restatement is drift.
- **Classify before writing**: a tutorial is a path, a reference is a lookup scope; the kind fixes the skeleton, the budget, and the reader.
- **Current state only**: history lives in decision records and incident postmortems; a current-state document says only what is.
- **Over budget, relocate first**: relocate → condense → raise the number last, with a reason.

**Anti-pattern**: assuming a file ships because it exists; hand-editing a generated page; copying a list into a document that source or a generator already owns; raising a budget number instead of moving the content to the home it belongs in.

---

## The one question every station must answer

| Station | Ask yourself |
|---|---|
| 1 Intent | Which class does this request belong to? (That decides which later rules apply to it.) |
| 2 Proposal | What did I reject? Does every acceptance criterion carry its id and its owning check? |
| 3 Decision | Did a fact change, or did a decision change? Does this capability have a current consumer, and are all three roles present? |
| 4 Contract | Is the interface in a form a machine can compare, or am I restating it? |
| 5 Implementation | Definition / implementation / consumer — all three? Who is consuming it **right now**? |
| 6 Verification | Does every criterion's id have a check that can go red? Have I actually seen it red? Is this test still right under the real CI topology? |
| 7 Review | Is the reviewer the same source as the author? Is the evidence an external observation? |
| 8 Integration | Did I preflight before landing? After rewriting history, did I confirm everything again? |
| 9 Release | Did I install and run it **outside the repository** once? Am I publishing the bytes I verified? |
| 10 Evolution | How does the reader migrate? How do they know it worked? Where are the three version states recorded? |
| 11 Retirement | Which old document will mislead the next round? Should it be deleted, merged, or sealed? |
| 📄 Documentation | Whose home is this fact? Do I paste the original or link it? Is it `public` or `internal`? Is this the word this kit uses for it? |
