# Skills

This repository hosts a set of agent skills maintained by `stupidZZ`. Each
skill is a self-contained folder under `skills/` with a
`SKILL.md` (YAML frontmatter + Markdown instructions) plus any supporting
references, scripts, or assets the skill needs.

## Available Skills

| Skill | Version | One-liner | Docs |
| --- | --- | --- | --- |
| [`feishu-task-sync`](skills/feishu-task-sync/) | 0.3.21 | 飞书 Todo 后台同步 · 每小时同步 + 每天 11:00 摘要 + 心跳广播。需要飞书自建应用 + OAuth。 | [Install guide](skills/feishu-task-sync/README.md) · [Agent spec](skills/feishu-task-sync/SKILL.md) |
| [`domain-owned-refactoring`](skills/domain-owned-refactoring/) | 0.1.0 | Architecture refactoring workflow for ownership, domain boundaries, dependency direction, shared core gates, and stopping criteria. | [Agent spec](skills/domain-owned-refactoring/SKILL.md) |
| [`ml-rl-experiment-engineering`](skills/ml-rl-experiment-engineering/) | 0.1.0 | ML/RL experiment systems engineering: configs, rollout/reward/evaluation boundaries, provenance, artifacts, and reviewability. | [Agent spec](skills/ml-rl-experiment-engineering/SKILL.md) |
| [`research-methodology`](skills/research-methodology/) | 0.3.0 | End-to-end research methodology: question framing, experiment design, analysis, report writing, and method distillation. | [Agent spec](skills/research-methodology/SKILL.md) |

`zz-wiki-context` is project infrastructure and has moved to
[`world-sim-dev/zz-wiki`](https://github.com/world-sim-dev/zz-wiki/tree/main/skills/zz-wiki-context),
where its loader, wiki schema, and bootstrap protocol can evolve atomically.

## Layout

```
skills/
  domain-owned-refactoring/  # Architecture refactoring by ownership and domain boundaries
  feishu-task-sync/      # Sync Feishu chats / docs / wiki into Feishu Tasks
  ml-rl-experiment-engineering/ # ML/RL experiment system design and review
  research-methodology/  # End-to-end research workflow
template/                # Minimal SKILL.md template used as a starting point
```

## Using a Skill from Codex

For local Codex discovery, symlink the skill into the user-level skills
directory:

```bash
mkdir -p ~/.agents/skills
ln -s ~/Code/skills/skills/research-methodology ~/.agents/skills/research-methodology
```

Then invoke it in Codex with `/skills` or explicitly in a prompt:

```text
$research-methodology
```

Restart Codex if a newly linked skill does not appear.

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

Use SemVer per Skill. Bump a Skill's frontmatter `version` whenever behaviour
or references change. Keep the table above in sync for user-facing discovery.
