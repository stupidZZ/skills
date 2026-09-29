# Skills

This repository hosts a set of agent skills maintained by `stupidZZ`. Each
skill is a self-contained folder under `skills/` with a
`SKILL.md` (YAML frontmatter + Markdown instructions) plus any supporting
references, scripts, or assets the skill needs.

## Available Skills

| Skill | Version | One-liner | Docs |
| --- | --- | --- | --- |
| [`feishu-task-sync`](skills/feishu-task-sync/) | 0.3.21 | 飞书 Todo 后台同步 · 每小时同步 + 每天 11:00 摘要 + 心跳广播。需要飞书自建应用 + OAuth。 | [Install guide](skills/feishu-task-sync/README.md) · [Agent spec](skills/feishu-task-sync/SKILL.md) |
| [`software-design`](skills/software-design/) | 0.1.0 | Software ownership, domain contracts and behavior-preserving architectural changes. | [Agent spec](skills/software-design/SKILL.md) |
| [`ml-rl-experiment-engineering`](skills/ml-rl-experiment-engineering/) | 0.3.1 | Protect objectives, reward/evaluation meaning and reproducibility during implementation. | [Agent spec](skills/ml-rl-experiment-engineering/SKILL.md) |
| [`research-methodology`](skills/research-methodology/) | 0.3.2 | End-to-end research methodology: question framing, experiment design, analysis, report writing, and method distillation. | [Agent spec](skills/research-methodology/SKILL.md) |
| [`task-first-ui-ux`](skills/task-first-ui-ux/) | 0.2.0 | User-facing information, documentation navigation and verified interaction repair. | [Agent spec](skills/task-first-ui-ux/SKILL.md) |

## Choose By The Decision, Not The Repository

| Example request | Relevant entry and scope |
| --- | --- |
| Design a new API or revise an existing object's lifecycle | software-design: domain contracts |
| Split module ownership while preserving public behavior | software-design: refactoring |
| Correct reward aggregation or checkpoint-selection meaning | ml-rl-experiment-engineering |
| Plan a controlled experiment or review its conclusions | research-methodology |
| Repair a chart, drag interaction or docs search | task-first-ui-ux |
| Fix stale generated docs or duplicated schema facts | software-design: documentation sources |
| Correct a README typo | No specialized workflow needed |
| Restructure a trainer and change its reward contract | software-design for migration, ML skill for semantic invariants |

Select only needed references. An ML repository does not automatically need
the ML skill; new versus existing software does not choose different skills.
Combined tasks may use complementary capabilities without repeating workflows.

## Retired Entry Points

`contract-first-domain-design` and `domain-owned-refactoring` are now references
inside `software-design`. `project-wiki-maintenance` is split between software
documentation sources and UI documentation reading. No discoverable alias
skills are retained, so old and new entries do not compete for selection.

Installing a new skill does not uninstall an old standalone installation.
Likewise, upgrading the zz-wiki plugin does not remove user-level symlinks or
copies managed by another tool. Inspect the installed location and consumers,
confirm cleanup scope, then remove only the obsolete installation entry (not
its source checkout). Verify discovery in a new task; an existing conversation
can still contain instructions loaded before the change.

`zz-wiki-context` is project infrastructure and has moved to
[`world-sim-dev/zz-wiki`](https://github.com/world-sim-dev/zz-wiki/tree/main/skills/zz-wiki-context),
where its personal-context reader, updater and plugin are maintained.

## Layout

```
skills/
  software-design/      # Contracts, ownership and safe structural changes
  feishu-task-sync/      # Sync Feishu chats / docs / wiki into Feishu Tasks
  ml-rl-experiment-engineering/ # ML/RL experiment system design and review
  research-methodology/  # End-to-end research workflow
  task-first-ui-ux/      # Task structure, interaction repair and verification
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
