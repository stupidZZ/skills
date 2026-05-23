# Skills

This repository hosts a set of [Kian](https://heykian.com) Skills maintained
by `stupidZZ`. Each Skill is a self‑contained folder under `skills/` with a
`SKILL.md` (YAML frontmatter + Markdown instructions) plus any supporting
prompts, scripts, or assets the Skill needs.

The repository layout intentionally mirrors
[`anthropics/skills`](https://github.com/anthropics/skills) so the same
conventions and tooling can be reused.

## Available Skills

| Skill | Version | One-liner | Docs |
| --- | --- | --- | --- |
| [`feishu-task-sync`](skills/feishu-task-sync/) | 0.3.5 | 飞书 Todo 后台同步 · 每小时同步 + 每天 11:00 摘要 + 心跳广播。需要飞书自建应用 + OAuth。 | [Install guide](skills/feishu-task-sync/README.md) · [Agent spec](skills/feishu-task-sync/SKILL.md) |

## Layout

```
skills/
  feishu-task-sync/      # Sync Feishu chats / docs / wiki into Feishu Tasks
template/                # Minimal SKILL.md template used as a starting point
```

## Using a Skill from Kian

Kian discovers external Skill repositories through
`<KianInstall>/skills/repositories.json`. Add this repo's clone URL there to
make Skills installable from inside Kian:

```json
{
  "repositories": [
    "https://github.com/anthropics/skills",
    "https://github.com/stupidZZ/skills"
  ]
}
```

After Kian refreshes the Skill catalog you can enable individual Skills (for
example `feishu-task-sync`) and they will appear in the running agent.

## Authoring a New Skill

1. Copy `template/SKILL.md` into `skills/<your-skill-name>/SKILL.md`.
2. Fill in the YAML frontmatter (`name`, `description`) and instructions.
3. Add supporting prompts/scripts/assets alongside `SKILL.md`. Anything in the
   Skill folder is shipped together when Kian installs it.
4. Avoid committing personal state: secrets, tokens, OAuth callbacks, message
   caches, or workspace‑specific paths must stay out of the repo. See the
   top‑level `.gitignore`.

## Versioning

Each Skill maintains its own version in `SKILL.md` (`version` frontmatter
field) and an optional `CHANGELOG.md`. Use [SemVer](https://semver.org/):
`MAJOR.MINOR.PATCH`. Bump `MAJOR` for breaking changes (e.g. new required
OAuth scopes, incompatible state schemas) so users can decide when to migrate.

Repository‑wide releases are cut by tagging `vYYYY.MM.DD` or a SemVer of the
repo as a whole; that is independent from individual Skill versions.

## Disclaimer

Skills here are provided as‑is. Always inspect a Skill before letting it
operate against production accounts.
