# Experiment Design Reference

Use this reference when the task requires planning experiments, benchmarks,
ablations, stress tests, or follow-up runs.

## Question First

Before listing configs, write the research question as a comparison:

```text
Does changing X, while holding Y fixed, improve or reveal Z?
```

Good experiment design makes the changed variable obvious. If the research is
exploratory, still state the intended observation:

```text
We are probing whether the failure appears only at larger N or already exists
at small N.
```

## Variables

Track four categories:

- primary variable: the thing this comparison is allowed to change;
- controls: settings that should stay fixed;
- measured outcomes: metrics or qualitative signals;
- nuisance variables: differences that may affect the outcome but are not the
  intended explanation.

If a nuisance variable cannot be controlled, add it to the table and narrow the
claim.

## Baselines

A baseline belongs in the same comparison surface when the new result depends
on relative interpretation.

Examples:

- larger `N` belongs with smaller `N` results;
- smaller batch size belongs with the batch-size sweep;
- a new model belongs with the backbone table under the same method and budget;
- a new method belongs with old methods or a negative control.

Do not make the reader reconstruct the baseline comparison across unrelated
sections.

## Minimal Experiment Set

Prefer the smallest set that can answer the next question:

1. include a known success or failure baseline;
2. include the new condition;
3. include enough seeds or repeated samples to distinguish mechanism from noise;
4. add stress tests only when the baseline comparison remains visible.

Avoid full grids unless the research question requires interactions between
multiple variables.

## Fairness Checklist

Check whether these are fixed:

- task and target distribution;
- dataset or data generator;
- train steps or wall-clock budget;
- batch size, unless batch size is the variable;
- optimizer, learning rate, regularization;
- evaluation sample count;
- seeds;
- decoding, sampling, or integration budget;
- parameter count or model capacity, when comparing architectures or
  conditioning mechanisms.

If a control differs, do not hide it in prose. Add a table column such as
`steps`, `batch`, `eval samples`, `solver steps`, `sampling steps`, `params`,
`device`, or another domain-specific budget column.

## Evidence Quality

A useful experiment should support three statements:

```text
Observation: what changed in the result?
Interpretation: what mechanism does this suggest?
Boundary: what can this experiment not prove?
```

If the boundary is larger than the interpretation, run a narrower follow-up
before presenting the claim as a conclusion.
