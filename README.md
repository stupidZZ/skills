# Skills

This repository hosts a set of agent skills maintained by `stupidZZ`. Each
skill is a self-contained folder under `skills/` with a
`SKILL.md` (YAML frontmatter + Markdown instructions) plus any supporting
references, scripts, or assets the skill needs.

## Available Skills

| Skill | Version | One-liner | Docs |
| --- | --- | --- | --- |
| [`feishu-task-sync`](skills/feishu-task-sync/) | 0.3.19 | 飞书 Todo 后台同步 · 每小时同步 + 每天 11:00 摘要 + 心跳广播。需要飞书自建应用 + OAuth。 | [Install guide](skills/feishu-task-sync/README.md) · [Agent spec](skills/feishu-task-sync/SKILL.md) |
| [`research-methodology`](skills/research-methodology/) | 0.2.1 | 端到端研究方法论：问题定义、实验设计、运行分析、报告写作和方法沉淀。 | [Agent spec](skills/research-methodology/SKILL.md) |
| [`zz-wiki-context`](skills/zz-wiki-context/) | 0.1.0 | 选择性读取 zz-wiki，为任务加载个人上下文、项目记忆和 skill 路由信息。 | [Agent spec](skills/zz-wiki-context/SKILL.md) |

## Layout

```
skills/
  feishu-task-sync/      # Sync Feishu chats / docs / wiki into Feishu Tasks
  research-methodology/  # End-to-end research workflow
  zz-wiki-context/       # Personal wiki context loader
template/                # Minimal SKILL.md template used as a starting point
```

## Using a Skill from Codex

For local Codex discovery, symlink the skill into the user-level skills
directory:

```bash
mkdir -p ~/.agents/skills
ln -s ~/Code/skills/skills/research-methodology ~/.agents/skills/research-methodology
ln -s ~/Code/skills/skills/zz-wiki-context ~/.agents/skills/zz-wiki-context
```

Then invoke it in Codex with `/skills` or explicitly in a prompt:

```text
$research-methodology
$zz-wiki-context
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

Each skill maintains its own version in `SKILL.md` metadata and may have an
optional `CHANGELOG.md`. Use [SemVer](https://semver.org/):
`MAJOR.MINOR.PATCH`. Bump `MAJOR` for breaking changes (e.g. new required
OAuth scopes, incompatible state schemas) so users can decide when to migrate.

Repository‑wide releases are cut by tagging `vYYYY.MM.DD` or a SemVer of the
repo as a whole; that is independent from individual Skill versions.

## Disclaimer

Skills here are provided as‑is. Always inspect a Skill before letting it
operate against production accounts.
