---
name: cut-release
description: Use when cutting, tagging, or publishing a release, or when re-running a publication that may have partially completed — to publish exactly the bytes that were rehearsed, from one recorded revision, with a publication step that is safe to repeat.
---

# Cut a release

A release is not "the build exited 0". It is one immutable artifact, verified outside the repository, published from a recorded revision by a step that does not rebuild and is safe to run twice.

## When to use

- You are tagging, versioning, or publishing a release.
- You built an artifact and are about to hand it to a registry or a distributor.
- A previous publication may have partially completed and you are re-running it.

## How

**1. Build one immutable artifact and record its identity.** Write down the revision and the artifact's content digest. Everything downstream addresses these bytes; a rebuild is a new revision, not the same release.

**2. Rehearse without publish credentials.** Produce the release-equivalent build and install it **outside** the repository into a throwaway directory, driven by a plain runtime.

- Use a throwaway `HOME` and config directory.
- Remove inherited host variables that can stand in for the artifact's requirements (`NODE_OPTIONS`, `NODE_PATH`, language-path variables, global git/npm hooks, preinstalled caches).
- Run the installed artifact, not the source tree.

A clean directory alone is not enough: the host's environment and user config travel with you.

**3. Publishing is a separate, explicit act — and it does not rebuild.** Upload the artifact this rehearsal verified. Never re-run the build during publication; that would ship bytes nobody rehearsed.

**4. Make publication idempotent.** Ask the destination what it already has:

| Destination state | Action |
|---|---|
| absent | publish |
| present, same content digest | skip — a re-run is safe |
| present, different digest | **fail** — the same version carries different content; cut a new version instead |

**5. Record the release** in `dev/release/<version>.md`: the exact revision, the artifact digest, the ordered evidence (rehearsal → outside install → publication), every externally perceptible break linked to its upgrade guide, and the rollback path. Ordered evidence means an interrupted run leaves a readable prefix.

**6. Retire the old version's notes** when the new one fully supersedes them, under [dev/README.md](../../../dev/README.md) stage 11.

## Verification

- `release-record` passes: `## Revision`, `## Scope`, `## Evidence`, `## Breaking changes`, `## Rollback` all present and non-empty.
- The outside-install command in the record ran in a throwaway directory with a scrubbed environment, and its output is quoted.
- The published digest equals the rehearsed digest; a rebuild did not intervene.
- Re-running publication either published the missing members or skipped on matching digests; it never overwrote a different content digest.

## Anti-patterns

- Publishing from an in-repo run, where `node_modules`, caches, and leftover output mask a broken payload.
- Rebuilding during publication, so the published bytes differ from the verified ones.
- Treating "the rehearsal passed" as "the release shipped".
- Overwriting a version with different bytes instead of cutting a new one.
- Publishing with the host's environment and user config in place, then calling it a clean install.
