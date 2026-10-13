# Contract: <name>

English | [中文](TEMPLATE.zh.md)

> Copy this file to `dev/contracts/<slug>.md`. A contract is not a description; it is a copy of a source that a check keeps honest. If a compiler can compare it, keep the interface in a source file and link; if a reader must see the exact declaration, paste it here and register the paste in `dev/contracts/mirrors.json`.
>
> One contract record per interface. Two records for one interface are two facts, and two facts drift.

## Interface

Name the exact symbol, key, file, or endpoint, and state in one sentence what a caller can rely on: the input it accepts, the output it returns, and the failure it raises. Write obligations, not behavior narration. If nothing depends on it yet, it is not a contract — delete the file.

## Source of truth

The one file and locator that owns this contract, and nothing else. Name a symbol or a `begin`/`end` marker pair the check can find — never "the code in general". When two files can change the interface, name both and say which one wins on conflict.

## Projection

What this file carries: the complete declaration, a body-stripped public subset, a generated table, or a link. Give the exact fence info string a check can match, and when the paste is a subset, say what was deliberately dropped (implementation bodies, private members, unshipped platforms). A projection nobody named is a copy nobody owns.

## Check

The command a reader runs to prove this file and the source still agree, and the command that rewrites the paste after a legitimate change. Both must be runnable exactly as written. The write command rewrites the paste; the check command fails when the file is stale. Pair every projection with a check, or delete the projection.

## Drift policy

What happens when the source changes: update the paste in the same change; add or remove the registration when blocks appear or disappear; when the interface is retired, delete the record or move it to `.agents/notes/archived/` with an `Archived:` date and never edit it again. A drift policy of "someone will notice" is not a policy.
