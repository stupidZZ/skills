---
name: software-design
description: Design or revise software ownership, domain objects, API/data contracts and module dependencies, including behavior-preserving architectural migrations. Use when these structural decisions are the task, not for every coding change, ML task, documentation edit or UI repair.
metadata:
  version: 0.1.0
---

# Software Design

Own structural decisions and their safe implementation, whether the system is
new or existing. Read current requirements, project guidance, code and tests
before prescribing a structure. A small bug fix need not become design work.

## Choose Only The Relevant Reference

| Decision needed | Read |
| --- | --- |
| Which objects, lifecycles, interfaces and failure contracts should exist? | [Domain contracts](references/domain-contracts.md) |
| How should existing ownership or dependencies change without losing behavior? | [Refactoring](references/refactoring.md) |
| Which document or schema owns a fact, and how should a derived view stay current? | [Documentation sources](references/documentation-sources.md) |

Do not load every reference. New versus existing code is not the routing rule:
an existing API may need contract design without a module migration. Combine
references only when the requested change needs both decisions.

## Work Proportionally

1. Identify the requested behavior or structural problem, its owning source,
   consumers and constraints. State what is outside this change.
2. Propose the smallest ownership or contract decision that solves it. Add shared
   layers or independent objects only for real consumers and coherent semantics.
3. For migrations, preserve behavior with focused tests before moving ownership.
   Implement one complete consumer path at a time; preserve concurrent work.
4. Verify contracts, failure cases and relevant entrypoints. Update the owning
   documentation, not a parallel design narrative. Stop when the requested
   consumer path works and remaining uncertainties are explicit.

## Neighboring Capabilities

- Experimental objectives, reward/metric meaning, selection or reproducibility:
  add `ml-rl-experiment-engineering` only if those semantics are at stake. Using
  an ML library alone is not a trigger.
- User-facing comparison, navigation, presentation and interaction: use
  `task-first-ui-ux`; do not add this skill unless ownership or contract decisions
  are also needed.
- Research questions, controls and evidence: use `research-methodology` when
  available. A study is not automatically a software architecture task.
- A simple README correction needs no design workflow. Documentation-source
  references apply when ownership or generation is the actual problem.

Report decisions, changed behavior, verification and unresolved risks. Follow
the user's existing artifact format; do not require a new document per stage.
