---
name: zz-wiki-context
description: |
  Load task-relevant context from the user's zz-wiki personal knowledge base.
  Use when a task mentions zz-wiki, cross-project memory, user preferences,
  prior decisions, project state, skill routing, or when Codex needs durable
  personal context before acting. Reads selectively instead of loading the
  entire wiki, and can check whether referenced skills from stupidZZ/skills are
  locally installed.
metadata:
  version: 0.1.0
  homepage: https://github.com/stupidZZ/skills/tree/main/skills/zz-wiki-context
  tags:
    - wiki
    - memory
    - context
    - routing
---

# ZZ Wiki Context

Use this skill to load the user's personal wiki as a context router. The goal
is to retrieve only the pages relevant to the current task, then apply them as
background while respecting the user's explicit prompt.

## Core Rules

- Treat zz-wiki as personal context, preferences, project memory, and routing
  guidance; do not treat it as a source of current external facts.
- User instructions in the current prompt override wiki content.
- Do not load the whole wiki by default.
- Always cite the local wiki paths you read when the loaded context influences
  the answer or file edits.
- If editing zz-wiki, keep changes small, update indexes/logs when needed, and
  commit/push if that is the repository norm.

## Locate The Wiki

Resolve the wiki root in this order:

1. `ZZ_WIKI_HOME` environment variable, if set.
2. `~/Documents/zz-wiki`.
3. Ask the user for the path if neither exists.

Use shell expansion rather than hard-coding a machine-specific path in outputs.

## Reading Workflow

### 1. Start With The Router

Read these first when present:

```text
START_HERE.md
wiki/index.md
wiki/rules.md
```

If token budget is tight, read `wiki/context/default-agent-brief.md` before
broader memory pages.

### 2. Pick Relevant Pages

Use the index and `rg` to find pages matching the task. Prefer targeted pages
over broad directories.

Common routes:

- current project or repository -> `wiki/projects/*.md`;
- user preferences -> `wiki/memory/working-preferences.md`;
- routing decisions -> `wiki/memory/memory-routing-policy.md`;
- context loading -> `wiki/memory/context-loading-policy.md`;
- cross-project rules -> `wiki/rules.md`;
- skill repository state -> `wiki/projects/stupidzz-skills.md`;
- recent changes -> `wiki/log.md`.

### 3. Summarize Loaded Context

Before acting on the context, produce or internally maintain:

```text
Loaded pages
Relevant facts/preferences
Uncertainties or stale-looking items
How this changes the current task
```

For short tasks, keep this summary implicit unless the user asks. For tasks
that modify wiki or skills, make it explicit.

## Skill Library Awareness

zz-wiki may refer to reusable skills stored in `stupidZZ/skills`. When the task
appears to require one of those skills:

1. Check the wiki's skill index, usually `wiki/projects/stupidzz-skills.md`.
2. Check whether the skill exists in the source repo, usually
   `~/Code/skills/skills/<skill-name>`.
3. Check whether it is installed for Codex discovery, usually
   `~/.agents/skills/<skill-name>`.
4. If the skill is missing or stale, recommend a symlink install or update.

Do not automatically install, update, delete, or overwrite a skill unless the
user has asked for that operation. Prefer symlinks so the Git repository remains
the single source of truth:

```bash
mkdir -p ~/.agents/skills
ln -s ~/Code/skills/skills/<skill-name> ~/.agents/skills/<skill-name>
```

If replacing an existing file or non-symlink directory, ask first.

## Writing Back To Wiki

Write to zz-wiki only when the user asks to update memory/wiki, or when the
task explicitly says to persist a durable decision.

When writing:

- update the smallest relevant page;
- update `wiki/index.md` if adding, moving, or renaming pages;
- update `wiki/log.md` for meaningful changes;
- preserve existing metadata style;
- commit and push when the repository policy says local wiki changes should be
  synchronized.

## Output Guidance

When the user asks "what context did you load?", answer with:

```text
Pages read
Key context
How it affects the current task
What I did not load
```

When the user asks to continue a project, use the loaded context to choose the
right project docs, repo, and skill, then proceed with the task.
