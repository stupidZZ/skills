---
name: template-skill
description: Replace with a description of the skill and when Kian should use it.
---

# Insert instructions below

Use this file as a starting point. The minimum required frontmatter is:

- `name`: lowercase identifier, hyphen-separated.
- `description`: one or two sentences describing when Kian should activate the
  Skill.

Optional frontmatter that this repository recommends:

- `version`: SemVer string for the Skill itself.
- `homepage`: link to documentation or repository for the Skill.
- `tags`: array of free-form labels.

The body of `SKILL.md` is the runbook Kian follows when the Skill is active.
Keep instructions concrete and reference any sibling files (`prompts/`,
`scripts/`, …) with relative paths.
