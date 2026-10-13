# Plain language

English | [中文](plain-language.zh.md)

The other half of the vocabulary problem: [glossary.md](glossary.md) fixes what a word means *inside* this kit, and this page says how to say it to someone who does not share that vocabulary. A person reading a reply, a pull-request description, a release note, a post-mortem summary, or a commit message has not read the glossary, so a term they were never given is a term they cannot use.

## How to use this table

- Find the term here before writing to a human; if it is missing, write the everyday sentence instead of the term and add the term here afterwards.
- Each entry gives the plain sentence and one replacement example. The example is the point: it is what the swap looks like in a real sentence.

## When to expand

1. **First use in human-facing text.** Expand once, or replace it outright — a reply, a PR description, a release note, a post-mortem summary, a commit message.
2. **Never assume a word was granted.** The glossary is the agent's vocabulary, not the reader's; the reader only has the words already in the message.
3. **One new word at a time.** Two invented words in one paragraph is a paragraph the reader skips.
4. **Name the actor and the fact** rather than the mechanism's metaphor: write what changed, what runs, and what the reader must do.
5. **Documents are exempt.** Records, contracts, and the standing documents exist to build vocabulary, so they use the terms and link the glossary.

## Terms and their plain meaning

### seam

A part you can swap for a different implementation without touching the rest.

*Instead of* "the shell seam rejected it" → *say* "bash-local and bash-sandbox are two implementations of the same shell capability, and this one rejected the command".

### surface

The group of files a change touches, together with the checks that must run when one of them changes.

*Instead of* "this is off-surface" → *say* "no check is declared for this file, so nothing will run for it automatically".

### evidence

The commands that were actually run, and what they printed — as a file, one verdict per command.

*Instead of* "that is not evidence" → *say* "that command has not been run, so nothing here shows what it would print".

### gate

The checks that must pass before a step counts as done.

*Instead of* "the gate is red" → *say* "`pair-docs --check` failed: the two sides of this pair no longer match".

### projection

Something generated from one source; editing it is editing the wrong file.

*Instead of* "update the projection" → *say* "edit `tools/workflow.json`, then re-run `gen-docs.py`".

### mirror

A copy of a block that is compared against its original, so it cannot drift unnoticed.

*Instead of* "keep it in sync by hand" → *say* "this block is registered in `dev/contracts/mirrors.json`, so the check fails the moment it stops matching the source".

### empty corpus

The check found no file to look at, so it proves nothing and is rejected as void.

*Instead of* "the check is green" → *say* "this check matches no file, and a check that cannot fail is rejected".

### station

One step of the workflow the kit is built around, from intent to retirement.

*Instead of* "we are at stage eleven" → *say* "this is the retirement step: delete the record, or seal it".

### criterion

One item on the "done" list, with the check that would fail behind it.

*Instead of* "acceptance criteria were met" → *say* "each item named a check, and every one of those checks passed".

### seal / frozen

Locked for good: the file's bytes and its date are recorded, and it may never be edited again.

*Instead of* "archive it and tweak later" → *say* "sealing freezes it; to change the content, unseal it and write a new record".

### pair / counterpart

The same page in two languages: the English original and its Chinese twin.

*Instead of* "update both sides" → *say* "edit the English page, edit its Chinese counterpart, then re-record the pair".

### canonical

The side that other translations are made from. It says which file is authoritative, not which file is right.

*Instead of* "the canonical truth" → *say* "English is the side this file is translated from".

### budget

A size limit on a file, in words or in lines.

*Instead of* "it violates the budget" → *say* "this page is 1,353 words and its limit is 1,400".
