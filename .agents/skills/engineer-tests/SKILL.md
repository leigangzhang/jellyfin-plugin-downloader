---
name: engineer-tests
description: Use when writing or changing a test, a fixture, a test helper, or a CI lane — and when a test fails only sometimes, only in CI, or only on another platform — to pick the right tier, keep the test isolated from its environment, and prove it can go red.
---

# Engineer tests

A test is evidence, not decoration. Before writing one, name the behaviour it owns and the command that runs it; after writing it, make it fail on purpose.

## When to use

- Adding or changing a test, a fixture, a helper, or a CI lane.
- A test passed locally and failed in CI, or passed in CI and failed locally.
- A test "needs" a longer timeout, a retry, or a sleep to pass.
- Deciding which tier a new criterion belongs to.

## How

**1. Pick the tier from the claim, not from habit.** The policy in [testing.md](../../../docs/testing.md) owns the list. In short: unit for edge cases, error paths, ordering, and races; a real entry run for "works the way it ships"; a golden or replay file for visible output; coverage to prove every line has an owner. A claim with no tier is not yet a claim.

**2. Write the failure first.** Introduce the regression, watch the intended assertion fail, then fix it and watch it pass. If you cannot make it fail, you have not written a test. For a static or registration guard, delete the registered subject and watch the gate go red.

**3. Own every resource you acquire.** Allocate atomically (`listen(0)`, `mkdtemp`, a unique namespace per test); never check availability and claim it later. Register cleanup immediately after acquisition so a failing assertion still releases it, and dispose to quiescence — awaiting the owned completion signal, not just calling `close()` or `kill()`.

**4. Synchronize on state.** Wait for a readiness event, a handshake, an owned promise, or an observable condition. A sleep is not evidence that setup finished. When time itself is the subject, inject or fake the clock, and restore real timers. To prove a race, use a barrier so the operations genuinely overlap; repeating the test is not a race test.

**5. Contain process-global state.** Environment, working directory, fake timers, locale, module registries, and global hooks are exclusive mutable resources. Capture whether the original value was absent or present, restore exactly that, and keep an `afterEach` fallback for a failure before your local `finally`.

**6. Do not mock inside the expensive boundary.** Mock the model, the network, the clock, or another genuinely non-deterministic edge — keep everything downstream real. A hand-rolled stand-in proves that the bridge moves bytes, not that the shipping component behaves as asserted.

**7. Take the lane's budget.** A per-case timeout overrides the runner's default rather than yielding to it, so a smaller literal silently lowers what CI granted. Raise the hook budget together with the test budget, and only tighten a bound for a named reason.

**8. Prove it, then report the command.** Report the exact command and its observed result. Never describe a retry, a skip, or pending CI as passing.

## Verification

- `python3 tools/change-scope.py --base <verified-base-ref>` names the `tests` surface, and you ran the owning test.
- The test or guard was watched failing for the regression it pins (a negative control), and passing after the fix.
- `python3 tools/check-invariants.py` still passes, and `--self-test` if you changed a declared check.
- No resource is left running, no global is left mutated, and no retry was added to mask a deterministic failure.

## Anti-patterns

- **A test that cannot fail.** An assertion that is true by construction reads like protection and proves nothing.
- **Sleeping instead of waiting.** A fixed delay is not a readiness signal, and it makes the test slower *and* flakier.
- **Timeout inflation as a fix.** Raising a number without naming the awaited state hides the defect until it returns under load.
- **Cleaning up only on the happy path.** Cleanup registered after the assertions leaks the resource exactly when the test fails.
- **Serializing the whole suite** because one fixture is unsafe: a sequential block cannot protect a host resource from another file, process, or job.
- **Mocking past the boundary.** Replacing real downstream behaviour turns a product test into a test of the mock.
