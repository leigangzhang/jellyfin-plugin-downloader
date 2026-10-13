# Capability: `<key>`

English | [中文](TEMPLATE.zh.md)

> This file is the shape of one entry in [registry.json](registry.json). It is not itself an entry. A capability is a thing that can be replaced without its consumers changing; it has **three roles**, and a file or package may hold several of them only while they are genuinely one concern.

## The entry

```json
{
  "key": "<stable-name>",
  "kind": "seam",
  "definition": "<path that owns the interface>",
  "providers": ["<path that implements it>"],
  "consumers": ["<path that uses it>"],
  "note": "<why these roles exist, and why they do or do not split>"
}
```

## Fields

| Field | Rule |
|---|---|
| `key` | Stable name, unique in the registry. It must appear verbatim after a line-leading marker in the source that owns the capability — by default `# capability: <key>`. |
| `kind` | One of `seam` / `core` / `service` / `bundle`. Only `seam` claims the three roles. |
| `definition` | The one path that owns the interface. It must exist. |
| `providers` | Paths that implement the interface. A `seam` needs at least one. |
| `consumers` | Paths that use the interface now. A `seam` needs at least one. |
| `note` | Why the roles look like this. A non-`seam` kind must say here why it is **not** a seam. |

## The three roles

- **Definition** — the interface and the vocabulary. It may be an abstract declaration, a registry, or a concrete class that other code swaps behind; it is never "whatever the first implementation happens to export".
- **Provider** — an implementation registered against that definition. A second provider is the usual reason to split the roles apart.
- **Consumer** — code that programs against the definition and never against a provider's types.

**One role alone is not a capability.** A definition with no provider is a wish; a provider with no consumer is a liability; a consumer coupled to one provider is not replaceable.

## Splitting rules

1. **Don't split preemptively.** One conceivable provider and one consumer stay in one file or package until the second one appears.
2. **Require a current consumer.** An abstraction, option, or compatibility path with no current consumer is rejected, not parked.
3. **Split on differing rates of change.** Separate the roles only when they change for different reasons — replacing a provider must not force the consumer's contract to move.
4. **Justify the exception.** A `core` / `service` / `bundle` entry is not a failure; leaving the reason unwritten is. Write it in `note`.

## Keeping the registry honest

The registry is hand-written; existence is not. Every capability carries a line-leading declaration in its owning source — by default `# capability: <key>`, with the marker string configured per project — and the `capability-registry` check requires the declared keys and the registered keys to be **exactly equal**:

- a declaration with no entry → unclassified capability;
- an entry with no declaration → stale registry row.

The declaration must be the first non-space text on its line, so a comment that merely mentions the marker does not register anything.

Rules that apply to every entry: keys are unique; `definition` exists; every provider and consumer path exists; a `seam` has at least one of each role. Adding a capability means adding the marker, the registry entry, and any record in the same change.
