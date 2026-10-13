# Minimal agent workflow kit

English | [中文](kit.zh.md)

A set of templates you can copy straight into any repository, distilled from DeepSeek Harness into the smallest working implementation of the **eleven-station lifecycle**:

1. **Budget the always-loaded context** — the root rules file stays short, and every rule has exactly one home.
2. **Leave a trace for every decision** — a proposal writes down what it rejected, and a decision writes down what it cost.
3. **Turn conventions into checks that can go red** — a rule nobody can script does not exist.
4. **Turn incidents into checks** — keep "what was green then", and one gate you have watched fail.
5. **Give the lifecycle a skeleton** — every station produces something decidable, and what you cannot decide may not enter the next station.
6. **Let skills decide when to act** — templates answer "what does it look like" and gates answer "prove it was done right"; the missing middle is when to read which one.
7. **Design, then implement, then call it a capability** — the interface and the acceptance criteria live in the proposal, and the registry answers whether it earns replaceable roles.
8. **Make every acceptance criterion name the check that will go red** — a criterion keeps its id when the record moves; one you cannot trace is a wish, not a criterion.
9. **Give the test strategy one home** — tiers, changed-surface evidence, determinism, negative controls, and the flake policy live in [testing.md](testing.md), and the `engineer-tests` skill decides when each rule applies.
10. **Distrust the green signal by default** — every "pass" has to say what makes it evidence, and distrust is spent only across a boundary and where a signal can lie ([skill](../.agents/skills/apply-distrust/SKILL.md)).
11. **Give every fact one home** — a document first gets a kind, and the kind fixes its skeleton, budget, and reader; a document both a human and an agent must act on becomes a **pair**: an English canonical file, a Chinese counterpart, and a consistency record ([documentation.md](documentation.md), [i18n.md](i18n.md)).
12. **Let the old thing actually disappear** — a breaking change writes its migration path in the change that introduces it; a consumed artifact is append-only, and a retired record is sealed and frozen ([evolution.md](evolution.md)).

It assumes no language, framework, or toolchain: the only implementation is **five zero-dependency Python 3 scripts** under `tools/`; everything else is Markdown and JSON.

> **Read [the lifecycle map](../dev/README.md) first** — the full statement of the eleven stations, each station's entry and exit, its gates, and its anti-patterns live there. This file covers only how to install the kit and where to go next.

---

## 30-second start

Copy the files for your tier; [the map](../dev/README.md) describes the three tiers. This path assumes an empty repository — for one that already has code, documentation, and consumers, [the order changes](adopting.md).

Minimum tier: one person iterating fast, no users yet — release, evolution, the archive, rejected records, and postmortems stay out.

```sh
cp -r AGENTS.md CLAUDE.md .agents docs dev tools tests <your-repo>/
rm -rf <your-repo>/dev/{release,upgrade-guide,postmortem} <your-repo>/.agents/notes/{archived,rejected}
```

Delivery tier: a public interface, and other people already using it — add release and evolution.

```sh
cp -r AGENTS.md CLAUDE.md .agents docs dev tools tests <your-repo>/
rm -rf <your-repo>/dev/postmortem <your-repo>/.agents/notes/{archived,rejected}
```

Long-lived tier: everything — the archive, rejected records, and postmortems join here.

```sh
cp -r AGENTS.md CLAUDE.md .agents docs dev tools tests <your-repo>/
```

Every document outside the exclusion table of [i18n.md](i18n.md#scope-and-exclusions) ships as a pair: each copy command brings `dev/README.md` together with its `dev/README.zh.md` and its `README.i18n.yaml`. Keep one language only if you must, delete the other side, and comment out the `i18n` surface check in the same change — an empty corpus is red, and a check nobody can satisfy is worse than no check. `CLAUDE.md` is a symlink to `AGENTS.md`, for a harness that reads that name; delete it if yours does not.

> **The kit ships no records of its own.** Inside `.agents/notes/`, each lifecycle folder's `TEMPLATE.md` is the skeleton you copy, and under `dev/evidence/` the only file is `TEMPLATE.md`. A copy made from an older revision may still carry that revision's own decision history and its runs — those are not your project's, so delete them:

```sh
find .agents/notes -mindepth 3 -type f \( -name '*.md' -o -name '*.zh.md' -o -name '*.i18n.yaml' \) ! -name 'TEMPLATE.*' -delete
find dev/evidence -mindepth 1 -type f ! -name 'TEMPLATE.md' -delete
```

> Every directory keeps its `TEMPLATE.md` afterwards, so the decision checks do not go red on an empty corpus. **For every stage you do not install, declare it `none` in [tools/tiers.json](../tools/tiers.json)** — the switch turns it into a declared absence, and the checks that own nothing but its home stop running ([the map](../dev/README.md) has the ladder). Leave the files there and the gate is red, which is the point: the switch states intent, the tree states fact.

Then edit one file: put your project's real paths and real commands into [tools/workflow.json](../tools/workflow.json) — the surface patterns, each evidence command, and the check patterns — and self-check that configuration with `python3 tools/run-evidence.py --check`. Finally run the commands listed in [AGENTS.md](../AGENTS.md#commands) and confirm they really work in your repository.

> `.agents/skills/` carries fourteen skills; delete the ones you do not use. **Do not empty `.agents/skills/`**, though: `skill-record` matches no file and fails the empty corpus. Point your host's skill loader at `.agents/skills/`: a `description` is only a matching condition, so a directory nothing loads is an article nobody opens. `tests/` holds the kit's **own** contract tests (standard-library `unittest` only): they are both the Unit command in [testing.md](testing.md) and the proof that this design has a command that really runs.

---

## Where to go

| Your question | Where to go |
|---|---|
| What is this kit, and how do I install it? | this file |
| What does each of the eleven stations produce, and which gate decides it? | [the map](../dev/README.md) |
| How should a document be written, budgeted, and published? | [documentation.md](documentation.md) |
| Are these documents bilingual, and how do the two sides stay in step? | [i18n.md](i18n.md) |
| How does a break leave a migration path, and how does a record retire or get sealed? | [evolution.md](evolution.md) |
| How does this repository decide that something is "verified"? | [testing.md](testing.md) |
| What are the standing rules? | [AGENTS.md](../AGENTS.md) |
| What do the five scripts do, and where do they stop? | [tools/README.md](../tools/README.md) |
| How do I extend the kit itself? | [extending.md](extending.md) |
| I am installing this into a repository that already exists — what changes? | [adopting.md](adopting.md) |
| What is a gate checking right now? | [check-catalog.md](check-catalog.md) |
| Which documents ship, and which stay in the repository? | [publish.json](publish.json) |
| What does this word mean here? | [glossary.md](glossary.md) |
| How do I explain a term to someone who has not read this kit? | [plain-language.md](plain-language.md) |
| Why was a decision made this way, and what did it give up? | [.agents/notes/README.md](../.agents/notes/README.md) |

Every home in this repository, and what each one owns, is indexed once in [docs/README.md](README.md).

## Deliberately not done

Seven things this kit refuses on purpose — no paired skills or instruction files, no CHANGELOG, no coverage percentage, no approval weights, no built-in CI, no "run everything for every change", and no judgement of content quality — and the reason for each lives where its subject lives, indexed in [docs/README.md](README.md).
