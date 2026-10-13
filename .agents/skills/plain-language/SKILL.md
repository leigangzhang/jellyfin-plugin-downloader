---
name: plain-language
description: Use when writing to a human — a chat reply, a pull-request description, a release note, a post-mortem summary, or a commit message — where a term the reader was never given reads as noise.
---

# Write to the human in their own words

[Glossary](../../../docs/glossary.md) fixes what a term means **inside** this kit, and that vocabulary belongs to the agent, not to the reader. The lookup for saying it differently is [plain-language.md](../../../docs/plain-language.md).

## When to use

- Any text a person reads: a reply, a PR description, a release note, a post-mortem summary, a commit message.
- When a sentence would work only for someone who has already read `docs/`.
- Not for documents: records, contracts, and standing pages exist to build vocabulary, so they use the terms and link the glossary.

## How

**Expand once, then use the term.** The first time a compressed term appears in human-facing text, give the everyday sentence — the plain meaning comes before the term, never after it in a parenthesis nobody reads.

**Never assume a word was granted.** If the message does not carry the word, the reader does not have it. Two invented words in one paragraph is a paragraph the reader skips.

**Name the actor and the fact.** Write what changed, what runs, and what the reader must do — not the mechanism's metaphor. `bash-local rejected the command` beats `the shell seam rejected it`.

**Replace the whole diagnosis, not only the noun.** "The gate is red" becomes "`pair-docs.py --check` failed: the two sides of this pair no longer match" — the reader needs the failing check and what it saw, not a colour.

## Verification

Re-read the text as someone who has read none of this kit's documents: every term either carries its plain meaning at first use, or is a word that already appeared earlier in the same message. If a term from the glossary appears with neither, fix it before sending.

## Anti-patterns

- Parenthetical glosses stacked after jargon: `the seam (a swappable capability) failed` still makes the reader parse the jargon first.
- Expanding in the first sentence, then using three more terms the reader has never seen.
- Writing to a human as if the glossary were shared context.
