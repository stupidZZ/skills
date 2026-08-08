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

## Protocol Generations

When an exploratory round has accumulated confounds, define a new protocol
generation before drawing stronger conclusions. A good reset states:

- which old rows are exploratory only;
- the research questions and comparison axes for the new round;
- fixed controls and allowed changed variables;
- required metadata columns for every table;
- convergence checks and stopping or probe rules.

Do not mix rows from different protocol generations unless the table includes
the differing budget or protocol columns and the conclusion is narrowed.

## Scaling And Compute Limits

Scaling experiments are especially easy to confound. When increasing problem
size, keep batch size, train steps, evaluation samples, sampling or integration
budget, optimizer, seeds, and model family fixed unless one of them is the
explicit variable.

If hardware forces a different budget, classify the run as one of:

- full-budget row: comparable to the rest of the axis;
- budget probe: tests whether a failure may be unconverged;
- compute probe: estimates feasibility or early dynamics only;
- stress test: deliberately changes the protocol to expose a limit.

Keep probes near the scaling table they inform, but do not include them in the
same ranking as full-budget rows.

## Batch Size And Execution Details

For distribution-matching methods, batch size can be part of the estimator, not
just a throughput knob. Treat effective batch size as a protocol variable.

Record execution details separately when needed:

- effective batch size: samples contributing to one optimizer update;
- micro-batch size: memory split used to accumulate that update;
- estimator caveat: whether splitting preserves the objective.

Gradient accumulation preserves per-sample mean losses such as MSE or flow
matching. It does not preserve a batch-level objective such as MMD unless the
implementation still computes the same pairwise terms over the full effective
batch.

## Convergence Checks

A failed final metric is not enough evidence for an architectural or objective
limitation. Check whether the training evidence supports convergence:

- loss tail mean, variance, and slope;
- metric trend, not just final metric;
- stability across seeds;
- longer-budget probe for important negative results;
- alternate learning rate or optimizer only when the loss is noisy or divergent.

If a row is still improving, label it budget-limited. If a row is valid but not
calibrated, separate the validity conclusion from the distribution-match
conclusion.

## Evidence Quality

A useful experiment should support three statements:

```text
Observation: what changed in the result?
Interpretation: what mechanism does this suggest?
Boundary: what can this experiment not prove?
```

If the boundary is larger than the interpretation, run a narrower follow-up
before presenting the claim as a conclusion.
