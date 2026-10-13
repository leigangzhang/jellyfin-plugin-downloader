# testing.md

English | [中文](testing.zh.md)

How this repository decides what "verified" means. [dev/README.md](../dev/README.md) stage 06 owns the lifecycle entry and exit; this file owns the policy. The rules below hold for any repository; the commands are this kit's own, so every one of them is known to run.

## Tiers

Every tier answers a question the others cannot. The commands below are **this kit's own** — they run on the kit, which is the smallest working example of the policy. Replace them with your project's commands when you copy the kit in.

| Tier | Command | It proves | It cannot prove |
|---|---|---|---|
| Unit | `python3 -m unittest discover -s tests -v` | the kit's own contracts: policy sections, config shape, evidence classification; one module per subject, with the shared fixtures in [tests/harness.py](../tests/harness.py) | your project's behaviour |
| Record checks | `python3 tools/check-invariants.py` | every declared convention holds on the real tree | that the convention is the right one |
| Guard self-test | `python3 tools/check-invariants.py --self-test` | each check kind rejects an invalid fixture and accepts a valid one | that your `checks` entries are meaningful |
| Evidence selection | `python3 tools/change-scope.py --base <verified-base-ref>` | which surfaces changed, and what evidence they need | whether that evidence is sufficient |
| Evidence run + record | `python3 tools/run-evidence.py --base <verified-base-ref>` | the runnable evidence actually ran, and each command's verdict is written down — `PASS`, `FAIL`, or `UNKNOWN` for one that could not run | the manual entries, which no tool can judge |

Neither script fetches or guesses a base ref: pass a ref you just verified. Two rules for this table: a tier with no command is not a tier, and a tier that another tier already answers is deleted rather than maintained.

## Evidence per change

There is no universal local baseline. Name the change first, then pick the smallest evidence that **this change** can break.

| What changed | Evidence |
|---|---|
| Behaviour in one module | that module's owning test, filtered by name |
| A contract several callers read | the owning test plus each caller's test |
| A user- or model-visible output | the golden or replay case that owns that output |
| Documentation or decision records | the record checks (`check-invariants`) |
| A guard, a check, or this file | `--self-test`, plus a live violation you watched fail |
| Packaging, entry points, build config | one clean install outside the repository, then run it |
| A test, fixture, or CI lane | run that test; then prove it can fail |

**Report it as you run it; file it once per delivery.** `tools/run-evidence.py` reads the same surface map, executes every evidence entry that starts with a known runner, refuses to execute a placeholder, and prints each command's exit code. Entries it cannot execute are printed as **manual** — the report never calls them passing. The run writes nothing; `--record` leaves the delivery's one record under `dev/evidence/`, named for the surfaces it covered and the merge base it was verified against, and a further run of the same delivery with the same verdict writes nothing again. Evidence reaches a reviewer in the reply; the record is the durable copy of what that reply claimed.

**Iterate narrow; cross the boundary wide.** `python3 tools/check-invariants.py --changed <verified-base-ref>` runs only the declared checks whose patterns read the change, and prints how much of the whole set that was. The full set belongs to the delivery boundary — running it after every edit is the repetition this file forbids.

```sh
python3 tools/change-scope.py --base <verified-base-ref>            # what needs evidence
python3 tools/run-evidence.py --base <verified-base-ref>            # run it, report it
python3 tools/run-evidence.py --base <verified-base-ref> --record   # leave the delivery's one record
python3 tools/run-evidence.py --check                               # only verify the declared commands resolve
```

Report the exact command and its observed result. Do not repeat a check whose inputs have not changed, and do not describe a retry, a skip, or pending CI as passing.

## Test doubles

**Substitute only the expensive or non-deterministic edge** — a model, a network service, a clock, randomness — and keep everything downstream real. A hand-rolled stand-in proves that a seam moves bytes, not that the shipping path behaves as asserted.

- **Name the boundary you replaced.** A test that stands in for the database but asserts a query plan, or stands in for a queue but asserts delivery order, is asserting against its own substitute.
- **Keep the interface the code already calls.** A recorded exchange replayed, a server on a loopback port, or an in-memory implementation of the same interface leaves the code under test unchanged; a stub the test reaches into does not.
- **A substituted boundary is not coverage of it.** Green with a stand-in proves the call site; the tier that runs the real boundary is the one that proves the integration.

## Determinism

A test that passes on a quiet workstation and fails in CI is measuring the environment, not the code. Assume these layers overlap unless the configuration proves otherwise: tests in one file; separate files or worker processes; independent gate processes in one job; different CI jobs sharing one host.

- **Allocate resources atomically.** Bind a loopback port with `listen(0)` and read the port the server reports; never scan for a free port and bind it later. Use a private temporary root (`mkdtemp`) per test. Give shared databases, sockets, and output paths a unique per-test namespace.
- **Synchronize on state, never on time.** Wait for a readiness event, a handshake, an owned promise, or an observable condition. A sleep is not evidence that setup finished or teardown settled. When a test's subject is time, inject or fake the clock and restore real timers.
- **Contain process-global state.** `env`, `cwd`, fake timers, locale and timezone, module mocks, registries, and global hooks are exclusive mutable resources. Capture whether the original value was absent or present, restore exactly that, register the restore immediately, and keep an `afterEach` fallback for failures before the local `finally`.
- **Dispose to quiescence.** Register cleanup immediately after acquisition so a failing assertion still releases the resource. Calling `close()`, `abort()`, or `kill()` without awaiting the owned completion signal is incomplete teardown.
- **Respect platform semantics.** A value the operating system owns may not round-trip. Prefer an observation that holds everywhere; when a case genuinely cannot, exclude it on that platform with the reason named, rather than weakening the assertion for everyone.
- **Budget timeouts against the lane.** A per-case timeout overrides the runner's default instead of yielding to it, so a smaller literal lowers what CI granted. Raise the hook budget together with the test budget.

- **Resolve one plane, never two.** A run that takes some modules from source and others from a built artifact measures neither, and a stale artifact loads a second copy of the same state. Say which plane the test needs; when the subject is the published artifact, start the real entry point rather than the source path.

## Blocking and observational lanes

Not every check deserves a veto. Declare each lane as **blocking** (a red result stops the change) or **observational** (a red result must be reported and judged, but does not stop it), in the same place the lane is declared.

- **Demote a platform-specific failure to observational. Never delete it and never weaken its assertion.** Deleting loses the signal everywhere; weakening loses it on the platforms that were passing.
- **An observational failure is still reported.** It goes into the evidence record and the review with its command and output. A silently ignored observation is a deleted check with extra steps.
- **Give every observational lane a promotion path.** After a named number of consecutive green runs with no unexplained instability, promote it to blocking. Without a way back, observational lanes rot into a list nobody reads.
- **The required-checks verdict is the one job that must not break.** Whatever aggregates the blocking lanes runs on the least exotic runner available, installs nothing, spawns no work of its own, and only reads results. It alone decides whether a change may land, so its own availability outranks anything it reports.

A lane is not a tier: the [Tiers](#tiers) table says what each command proves, and this section says which results are allowed to stop a change.

## Prove a new guard

A guard only guards if the regression fails it. **No new assertion, check, or fixture is accepted without a negative control.**

- Introduce the regression (or the rejected case), watch the intended check fail, then revert and watch it pass.
- For a race, use a barrier to prove the operations actually overlap; running the test repeatedly is not a race test.
- For a corpus, schema, or registration guard, temporarily remove the registered subject and confirm the gate goes red in the direction you claim.
- Verify external state — files, events, logs, exit codes, disposal — instead of trusting the component's own report.
- Add the negative control to `--self-test` when the guard is a declared check, so the proof survives future edits.

A guard nobody has watched fail is a guess with a green light.

## Flake policy

A flaky test is a defect in the test or in the product, never noise.

A test that passes only when it runs alone is a defect in the test, not an unstable machine: it holds state another test also owns, or it depends on the order the runner happened to use. Fix the ownership or the order — serializing it is one of the rejected fixes below. These are not root-cause fixes and are rejected in review:

1. raising a timeout without naming the awaited state;
2. adding retries;
3. making all files serial;
4. swallowing an error or an unhandled rejection;
5. weakening an assertion;
6. normalizing unstable behaviour away;
7. adding a sleep before cleanup or before an assertion.

Two exceptions, both narrow and both named in the change:

- **External non-determinism.** A test whose subject is a real external provider may retry. Keep the retry at that boundary and nowhere else, and say which boundary it is.
- **Restoring a granted budget.** Raising a suite to the lane budget it already had, or sizing a bounded retry to the contention actually measured, names the awaited work and returns what the lane granted. Neither invents headroom around an unexamined wait.

Serializing a suite is also not isolation: a sequential block cannot protect a host resource from another file, process, or job. Narrow the exclusive scope or fix the allocation instead.

A shared fixture lives in its own module, never in another test file: collecting or importing a test file runs its cases a second time, so the fixture's setup and its real side effects happen twice.

## Known limits

`testing-policy` guarantees only that the seven sections of this file **exist and are not empty**. Whether these tiers are the right ones, whether the evidence map really catches regressions, and whether the flake policy is followed are all beyond the gate — that half belongs to [engineer-tests](../.agents/skills/engineer-tests/SKILL.md) and review. The same holds for every claim of the form "this command proves this tier": the kit will not run it for you.
