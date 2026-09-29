---
name: ml-rl-experiment-engineering
description: |
  Protect experimental meaning when implementing or debugging ML/RL objectives,
  rollout/data contracts, reward and evaluation metrics, checkpoint selection
  or reproducible configurations. Use when a change can alter what is optimized,
  measured or reconstructed; not merely because the repository uses ML.
metadata:
  version: 0.3.1
  homepage: https://github.com/stupidZZ/skills/tree/main/skills/ml-rl-experiment-engineering
  tags:
    - machine-learning
    - reinforcement-learning
    - experiments
    - configuration
    - ml-engineering
---

# ML/RL Experiment Engineering

For metric, configuration and historical-evidence counterexamples, read
[semantic lessons](references/semantic-lessons.md) when interpreting those boundaries.

Use this skill when code structure, configuration, or pipeline design can
change the meaning of an ML/RL experiment. The goal is not only cleaner code;
it is preserving what is being optimized, measured, compared, and reproduced.

For research planning, comparison or report writing without an implementation
semantics problem, use `research-methodology` instead. Add it only when both
research design and implementation meaning are in scope. For module ownership
or contract migrations, use `software-design`; this skill adds experiment-specific
invariants, not a second general architecture workflow. A UI repair or import
cleanup in an ML repository does not by itself require this skill.

## Core Rule

Make the experiment's semantics visible from configs, top-level lifecycle code,
metrics, and artifacts:

```text
objective -> rollout/data -> reward/signal -> evaluation -> selection -> artifact
```

If a reader must inspect deep implementation details to know what changed
between two runs, the system is not yet reviewable.

## When To Use

Use this skill for:

- designing or reviewing training, rollout, reward, evaluation, or checkpoint
  selection code;
- changing a pipeline where reward, evaluation or artifact semantics may drift;
- creating, pairing, validating, or reviewing experiment configs;
- deciding where config fields, provider settings, prompts, datasets, metrics,
  and artifacts belong;
- debugging confusing metrics, hidden inheritance, mixed budgets, or provenance
  gaps;
- preparing an experiment system for longer runs or cross-agent maintenance.

Do not use it as a substitute for domain science judgment. It protects
engineering semantics; the project still defines the model, algorithm, data,
and evaluation goals.

## Workflow

### 0. Read The Relevant Experiment Truth Sources

Inspect only the sources needed to establish the semantics at stake:

- `AGENTS.md`, README, docs, configs, schema definitions, and validators;
- training, rollout, reward, evaluation, logging, and checkpoint code;
- run artifacts, effective configs, reports, dashboards, and metric names;
- active or recent jobs that may rely on the current artifact contract;
- project-specific conventions for generated docs or run output.

Current user instructions and project docs override this skill.

### 1. Build The Semantic Map

Separate the roles that determine experiment meaning:

| Role | Question |
| --- | --- |
| training objective | Which signal changes model parameters? |
| rollout or data generation | How are samples produced? |
| reward or supervision | What contract creates the learning signal? |
| monitor | What held-out signal is observed during training? |
| evaluation | What evidence says the policy/model improved? |
| checkpoint selection | Which metric chooses the best checkpoint? |
| artifact/provenance | What lets the run be reconstructed later? |

Do not collapse these roles merely because they share one judge, model,
provider, dataset, or metric name.

### 2. Use Domain Names With Units And Cardinality

Prefer config and code names that reveal the object being counted or owned:

- `prompts_per_step`, not ambiguous `batch_size`;
- `candidates_per_prompt`, not vague `group_size`;
- `videos_per_batch`, not generic `batch`;
- `policy_comparison`, not calling every judge value `reward`;
- `generation_max_concurrency`, not generic `max_workers`.

If a field needs a long comment to explain its unit, owner, or lifecycle, fix
the name or move it to the owning config section.

### 3. Route Config Fields By Ownership

Place fields where they are consumed and where their lifecycle belongs:

- system prompt and policy identity -> policy/model config;
- source data and filters -> training dataset config;
- candidate sampling and exploration -> rollout config;
- provider request limits, polling, retries -> provider/generator config;
- reward contracts and aggregation -> reward config;
- evaluation prompts, references, and sample counts -> evaluation config;
- run name, seed, output path, tags -> experiment identity config;
- logger credentials or project names -> logging config, with secrets outside
  tracked files.

When backends have different capabilities, express their constraints with
capability-specific types or validated dictionaries rather than runtime surprises.

### 4. Keep Formal Experiment Configs Complete

Formal configs should be independently reviewable recipes:

- they expose complete final effective values for review;
- they use shared typed schema and cross-field validation;
- mutable inheritance must not silently change a formal paired arm; shared
  configuration mechanisms are acceptable when effective settings are frozen,
  inspectable and reproducible;
- they save JSON-safe effective config snapshots with run artifacts;
- they fail early on typos, illegal values, or unsupported capability combos.

It is fine to share schema, validators, builders, and execution code. It is not
fine to hide final experimental values behind a mutable inheritance chain.

For paired experiments, repeated final parameters are provenance, not waste.
Protect pair invariants with tests instead of making one arm inherit the other.

CLI overrides are for bounded probes such as sample limits, dry-run output
paths, or connectivity checks. Formal runs should be reproducible from the
standalone config plus saved effective snapshot.

For training recipes, make the basic settings executable and reviewable, not
merely mentioned in the plan:

- expose learning rate, warmup, effective batch size and total training length;
- for classification fine-tuning, compute and record training loss and class
  accuracy, plus held-out loss and task metrics; define whether training
  accuracy covers the current global batch or another explicit sample set;
- configure validation frequency, checkpoint saving frequency and retention
  separately, with explicit units; identify protected model-selection candidates
  so retention cannot silently delete them;
- verify that the configured metrics are actually computed, aggregated across
  microbatches/ranks, logged and displayed, and that evaluation and saving fire
  at their configured optimizer steps. Declaring a metric name is insufficient.

For autoregressive classification, class accuracy uses the label decision at
the answer position. Accuracy over answer-format or end tokens is not a
substitute; state the candidate-label prediction rule and keep it consistent
with validation. Different tasks may need different metrics, not these names
copied blindly. Use the existing trainer/logger boundaries rather than adding
an elaborate process to compensate for missing basic settings.

### 5. Separate Efficiency Mechanisms

Do not let one field mean every kind of parallelism:

| Mechanism | Meaning |
| --- | --- |
| concurrency | independent tasks happening at the same time |
| model batching | inputs in one model forward or provider batch |
| prefetch | I/O prepared before the consumer needs it |
| accumulation | multiple micro-steps that preserve one optimizer objective |
| evaluation budget | samples or comparisons used only for judging |

Changing an execution detail is only semantically neutral when it preserves the
same mathematical objective, data distribution, and evaluation protocol.

### 6. Preserve Objective, Monitor, Evaluation, And Selection Boundaries

Before interpreting metrics, identify what each metric is allowed to prove.

Examples:

- a group-relative training signal may change parameters but be meaningless as
  an absolute checkpoint curve;
- a held-out pointwise monitor can detect drift without being the training
  objective;
- a policy win rate against a fixed reference is evaluation, not automatically
  reward;
- checkpoint selection needs one explicit metric and tie-breaking policy.

Metric namespaces and report sections should preserve these distinctions.

### 7. Manage Current Configs, Historical Evidence, And Runtime State

Keep the source tree from depending on generated history:

- current formal configs live in the supported config area;
- tests use small fixtures with explicit purpose;
- old studies or obsolete configs move to archive with version context;
- `runs/` and checkpoints are runtime state, not source dependencies;
- stable reports cite explicit run IDs or artifact snapshots;
- generated dashboards or HTML views should be rebuilt from source truth rather
  than edited by hand.

If historical configs no longer run on current code, label them as provenance,
not templates.

### 8. Validate Before Long Or Expensive Runs

Before launching long training, paid API batches, GPU jobs, or large
evaluations:

- run schema validation and smoke tests;
- save or print the effective config;
- check output paths and overwrite behavior;
- confirm sample counts, seeds, and budget;
- verify reward/evaluation contracts on tiny data;
- confirm logger/project names without exposing secrets.

Ask for user approval before spending meaningful compute or money unless the
user has already authorized that run.

## Review Mode

When reviewing a diff or design, prioritize issues that can invalidate
comparisons:

- a config inherits mutable final values from another experiment;
- objective, monitor, evaluation, and selection are conflated;
- field names hide units, cardinality, or ownership;
- provider concurrency, model batch, and prefetch are controlled by one knob;
- artifacts do not include the effective config;
- historical runs are imported as source dependencies;
- a reward or evaluation semantic changed inside a broad refactor;
- tests check file placement but miss experiment invariants.

Recommend the smallest correction that restores reviewability and
reproducibility.

## Output Shapes

For system design:

```text
Experiment semantic map
Config ownership map
Objective/monitor/evaluation/selection boundaries
Artifact and provenance plan
Validation checks
Open risks
```

For config review:

```text
What this config claims to run
Hidden inheritance or override risks
Units and ownership issues
Validation gaps
Paired-experiment invariants
Required fixes before formal run
```

For refactoring:

```text
Current lifecycle
Target component responsibilities
Protected experiment semantics
Migration slices
Tests and artifact checks
Stop condition
```
