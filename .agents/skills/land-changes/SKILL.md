---
name: land-changes
description: Use when merging or landing completed work, or immediately after a rebase, amend, squash, or force-push — to re-verify the change at the revision that is actually landing instead of trusting evidence from a revision that has moved.
---

# Land changes

Landing is where a change stops being a review and becomes history. The evidence that matters is evidence about **the revision that lands**, so a rewrite or an advanced base voids everything gathered before it.

## When to use

- You are about to merge, squash, or push a change onto its base.
- You rebased, amended, squashed, or force-pushed after review — including a cascade that publishes before you can validate it.
- The base moved since you last ran the evidence.

## How

**1. Preflight against the live base and head.** Re-resolve both; never trust a branch name or an earlier report.

```sh
git rev-parse <head> <base>
python3 tools/change-scope.py --base <verified-base-ref>
```

**2. Treat each change independently.** One green aggregate, or a green top layer, does not prove the layers below it are ready. Re-read the actual diff against the current base; a base advance can change what the change even means.

**3. Choose the history deliberately.**

| History | When it fits | What it costs |
|---|---|---|
| Merge-forward | You want to preserve a checkpoint and avoid rewriting published history | an extra merge commit; each in-progress checkpoint must be kept |
| Rebase / amend / squash | Standalone work, or a base that must not accumulate merge commits | rewrites IDs; every earlier "it passed / resolved" is void |

After a rewrite, re-fetch the exact new head, re-read the diff, and re-run the affected surfaces' evidence before declaring it ready.

**4. Protect a rewriting push.** Observe the remote ref first, then push with the lease, and abort if it moved; never use raw `--force`.

```sh
git rev-parse origin/<branch>
git push --force-with-lease=<branch>:<observed-oid>
```

**5. Land one bisectable step at a time,** and re-run the affected evidence on the landed revision, not the pre-merge one.

**6. Clean up last.** Delete a branch, a worktree, or a temporary artifact only after confirming nothing still depends on it (an open change based on it, a worktree using it, a process holding it). A dependency blocks deletion; resolve the dependency first.

## Verification

- `change-scope.py --strict` exits 0 for the revision being landed.
- Every "passed / resolved" claim carries a run against the current head, not an earlier one.
- Any rewriting push used a lease with the observed OID; no raw `--force` appears.
- The landed revision re-ran the affected evidence; deletion happened only after a zero-dependency check.

## Anti-patterns

- Merging a large batch at once, so a failure cannot be bisected to a layer.
- Reusing a PASS from before a rebase, amend, or base advance.
- Letting an aggregate "all green" stand in for a single surface's evidence.
- Force-pushing with raw `--force`, or over a head that moved since you looked.
- Deleting a branch or artifact that another change still bases on.
