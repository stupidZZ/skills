---
name: research-methodology
description: |
  End-to-end research methodology skill. Use when the user asks Codex to plan,
  run, analyze, compare, review, or document research, experiments, benchmarks,
  ablations, empirical investigations, small-scale studies, or exploratory
  technical work. Covers question framing, hypothesis and variable design, fair
  experiment comparison, evidence analysis, report writing, and method
  distillation.
metadata:
  version: 0.2.1
  homepage: https://github.com/stupidZZ/skills/tree/main/skills/research-methodology
  tags:
    - research
    - experiments
    - benchmarks
    - methodology
---

# Research Methodology

Use this skill to help an agent behave like a research collaborator, not only a
report writer. The output should make the research question, evidence design,
execution, analysis, and conclusion boundaries explicit.

## Core Rule

Keep the research loop coherent:

```text
question -> hypothesis -> experiment design -> execution -> analysis -> report -> distilled method
```

Do not let implementation details, result tables, or one-off follow-up runs
detach from the question they are supposed to answer.

## Scope And Language

Translate this workflow to the current domain's own variables and evidence
type. Do not assume the project uses the examples in this skill; map local
concepts onto roles such as method, model or system, data, condition, budget,
metric, and boundary.

Respond in the user's language unless the target artifact, paper, report, or
repository convention requires another language.

## Workflow

### 0. Read Existing Context First

For continuation, analysis, or report revision tasks, read the existing project
context before proposing structure:

- README, docs, reports, tables, and task notes;
- configs, run metadata, logs, or result files that define the experiment;
- prior conclusions, caveats, and pending follow-ups.

Do not impose this skill's report shape before understanding the artifact the
user already has.

### 1. Frame The Research Question

Start by writing the question in operational terms:

- What phenomenon, claim, or mechanism is being investigated?
- What would count as success, failure, or partial evidence?
- What is the smallest experiment that can answer the next question?
- What is out of scope for the current round?

If the question is vague, propose a concrete version before designing runs.
For exploratory work, keep the question lightweight but still explicit.

### 2. Define Variables And Controls

Separate:

- changed variables: method, model, data, scale, budget, prompt, optimizer, etc.;
- fixed controls: seed set, data source, train/eval budget, sampling budget,
  hardware, preprocessing, metrics, and stopping rules;
- response metrics: what will be measured;
- confounders: what could explain the result besides the intended variable.

Prefer changing one primary variable per comparison surface. If multiple
variables must change, state the limitation before interpreting the result.

When the domain is not machine learning, rename these roles instead of forcing
ML vocabulary. For example, "model" may become policy, intervention, workflow,
instrument, simulator, dataset, or human process.

For detailed experiment design and fairness checks, read
`references/experiment-design.md`.

### 3. Place Each Experiment On A Comparison Axis

Every run should belong to a comparison axis:

- method comparison;
- backbone/model comparison;
- scale or difficulty comparison;
- data or batch-size comparison;
- budget or efficiency comparison;
- negative-control or sanity-check comparison.

Do not create isolated follow-up sections when the result only makes sense
relative to an existing baseline. Add the new result to the table or analysis
surface that owns the axis.

If the follow-up uses a different budget, keep it on the relevant axis when
that is still the meaningful comparison, but add explicit budget columns and
narrow the conclusion.

### 4. Implement And Run With Traceability

Make experiments reproducible enough for the current research stage:

- name configs after the comparison axis and changed variable;
- store the exact config with the run output;
- record seed, budget, device, and evaluation settings;
- keep local scratch artifacts out of commits unless they are the chosen data
  snapshot for a report;
- prefer small smoke tests before long runs.

Before launching long training runs, large benchmarks, paid API batches, GPU
jobs, or token-heavy evaluations, present the plan and wait for user approval
unless the user has already authorized that cost.

For implementation work, keep the code structure simple enough to support the
next experiment. Avoid abstractions that do not yet remove real complexity.

### 5. Analyze Evidence Before Writing Conclusions

For each comparison, distinguish:

- observation: what the table or trace shows;
- interpretation: what mechanism or limitation it suggests;
- boundary: what the evidence does not prove.

Use more than one metric when one metric can be misleading. Typical categories:

- validity or legality: does the output satisfy hard constraints?
- distribution match or calibration: does it match the full target, not just a
  valid subset?
- structure or dependence: did it learn the intended relation?
- stability: does it hold across seeds, subsets, or reruns?
- cost: parameters, runtime, samples, tokens, or human effort.

### 6. Write Or Revise The Report

When the user asks for a report, analysis page, benchmark summary, ablation
writeup, or review of an existing report, read `references/report-writing.md`.

The report should be self-contained: a reader should not need to inspect source
configs or logs to know what each row means, why it is comparable, or what
conclusion is justified.

### 7. Distill Reusable Method

At the end of a research cycle, identify what should persist:

- project docs: experiment-specific instructions and results;
- wiki/memory: cross-project context, user preferences, or project state;
- skill repo: only when the user explicitly asks to create or update a reusable
  skill;
- code: reusable scripts only when they remove repeated manual work.

Do not promote a one-off observation to a general method until it has survived
at least one real use or correction.

## Output Shape

For research planning, produce:

```text
Question
Comparison axes
Minimal experiment set
Controls and fairness caveats
Expected evidence
Execution plan
```

For research analysis, produce:

```text
What changed
What stayed fixed
Main observations
Interpretation
Boundaries
Next experiment
```

For report revision, lead with concrete issues: missing setting, missing
baseline, unclear labels, unfair comparison, misleading metric, unsupported
conclusion, or missing boundary.

## Anti-patterns

Avoid:

- running experiments before stating the comparison axis;
- adding a stress test as a standalone result when it belongs in a scaling
  table;
- reporting only the metric that makes a method look best;
- treating a budget-mismatched stress point as a fair ranking;
- writing conclusions that require the reader to jump between tables;
- turning a report-format rule into the whole research methodology.
