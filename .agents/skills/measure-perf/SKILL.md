---
name: measure-perf
description: Use when asked to make something faster, when a workload feels slow, or when designing a performance gate — to turn a vague "make it faster" into a measured user path and one small, behaviour-preserving fix.
---

# Measure performance

Survey broadly, follow measured cost, and reject attractive changes that do not improve the workload users actually run. This is guidance, not a quota.

## When to use

- A performance problem is reported or suspected.
- You are about to optimize something because it "looks slow".
- You are introducing or tightening a performance gate.

## How

**1. Agree on the endpoint before measuring.** Name the user-visible operation and the condition that means it completed. Fix the workload range, the resource constraints, and the stopping rule in advance. Keep component timings separate from end-to-end latency: a fast inner loop does not prove a fast page, and a fast page does not prove fast input response.

**2. Rank candidates by observed cost, not by suspicion.** A suspicious loop, an unused cache, or a large file is not evidence of a bottleneck. For each candidate name the caller that pays the cost, the repeated work, the expected complexity change, the smallest falsifiable intervention, and the behaviour that must stay stable.

**3. Build the measurement before the fix.** Write down a measurement card first:

| Field | Decision required |
|---|---|
| Operation | exact action and observable completion |
| Workload | fixed dimensions, distribution, and seed — and why it exercises ordinary and tail use |
| Entry path | the real path, with only the non-deterministic or expensive edges mocked |
| Clock | what is included, cold or warm, start and end |
| Memory | what is retained versus transient, and the baseline |
| Verdict | raw samples, the aggregate that decides, the limit, and the **negative control** |

Report every sample, not the best one. Never widen a limit, and never pick a lucky run, to make a regression pass.

**4. Prove the regression before removing work.** Run the unoptimized workload first and save the command, revision, environment, and raw numbers. Reduce the failing scenario until it still exercises the real bottleneck. Change **one causal factor at a time** and re-run both the focused scenario and its end-to-end parent.

**5. Require a negative control.** The tightened check must fail on the original code, or on a deliberate reintroduction of the cost. A threshold so generous that the regression still passes is not protection; one below the noise floor is not reliable either.

**6. Preserve behaviour.** Performance evidence complements functional evidence; it never replaces it. Do not truncate, skip, disable, or weaken semantics to reach a number. State any deliberate minor difference and pin it with the owning test.

## Verification

Report each result as: **workload → before/after absolute values and ratio → endpoint and memory semantics → behaviour evidence → negative control → exact commands → exclusions.**

Separate historical or reported numbers from freshly measured ones.

## Anti-patterns

- Optimizing from a profile you never took.
- Benchmarking an isolated function and claiming an end-to-end win.
- A cache that makes repeated benchmarks fast and retention unbounded.
- Landing a speedup whose end-to-end gain disappears.
- A performance gate nobody has watched fail.
