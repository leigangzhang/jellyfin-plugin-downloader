"""The skill guard: a description is a trigger, not a summary."""

from __future__ import annotations

import re

from .core import iter_files, section_body

TRIGGER_PREFIX = re.compile(r"^Use (?:when|before|after|during)\b")


def front_matter(text: str) -> list[str] | None:
    """Return the lines inside a leading '---' front-matter block, or None."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return lines[1:index]
    return None


def check_skill_trigger(root: Path, check: dict) -> list[str]:
    """Reject a skill whose description does not name when the skill loads.

    A skill is loaded by its description; a description that summarizes its own
    contents has silently left the agent's reach. Only the machine-decidable half is
    checked — the front matter exists and the description opens with a load trigger.
    Whether the wording reads like a trigger is left to review, because "reads like a
    summary" cannot be decided by a regex.
    """
    violations: list[str] = []
    for relative, text in iter_files(root, check["patterns"]):
        meta = front_matter(text)
        if meta is None:
            violations.append(f"{relative}: no '---' front-matter block, so nothing declares when it loads")
            continue
        fields: dict[str, str] = {}
        for line in meta:
            key, separator, value = line.partition(":")
            if separator and key.strip() in ("name", "description"):
                fields[key.strip()] = value.strip()
        if not fields.get("name"):
            violations.append(f"{relative}: front matter has no 'name:'")
        description = fields.get("description", "")
        if not description:
            violations.append(f"{relative}: front matter has no 'description:', so the skill has no load trigger")
        elif not TRIGGER_PREFIX.match(description):
            violations.append(
                f"{relative}: description must open with when the skill loads"
                f" ('Use when' / 'Use before' / 'Use after' / 'Use during'), not a summary: {description[:60]!r}",
            )
    return violations


SELF_TEST_CASES = (
    (
        "skill-trigger",
        {"id": "self-skill-trigger", "kind": "skill-trigger", "patterns": [".agents/skills/*/SKILL.md"]},
        {".agents/skills/a/SKILL.md": "---\nname: a\ndescription: Summarizes the testing policy.\n---\n\n## When to use\nx\n"},
        {".agents/skills/a/SKILL.md": "---\nname: a\ndescription: Use when a check fails to decide whether the failure is environmental.\n---\n\n## When to use\nx\n"},
    ),
)
