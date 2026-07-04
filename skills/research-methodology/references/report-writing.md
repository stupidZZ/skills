# Report Writing Reference

Use this reference when writing, reviewing, or revising a research report,
experiment report, benchmark report, ablation report, or empirical analysis.

## Core Standard

Every experiment section must be self-contained. A reader should understand
what the experiment asks, how it was run, what it compares against, what the
metrics mean, and what conclusions are or are not supported without opening
source configs or logs.

Each section should answer:

1. What question does this experiment answer?
2. What is the task, dataset, target distribution, or evaluation target?
3. Which variables change, and which controls stay fixed?
4. What method, model/backbone, conditioning, parameter count, dataset, and
   training/evaluation budget does each row use?
5. Should the table be read as a ranking, ablation, paired baseline comparison,
   scaling check, robustness check, or stress test?
6. Which metrics support the conclusion, and which metrics are auxiliary?
7. What can this experiment prove by itself, and what requires another table?

## Recommended Structure

Use this order unless the artifact requires a different one:

1. Questions: state the overall research questions before showing results.
2. Common protocol: define shared setup, budget, seeds, and core metrics.
3. Glossary: define repeated terms, abbreviations, and internal labels.
4. Experiment sections: include setting, comparison design, result table, and
   analysis.
5. Takeaways: include only conclusions supported by earlier tables.

## Section Template

### Header

- Name the experiment and comparison logic.
- Prefer "N scaling check" over "N=1024 results" when the result is meaningful
  only against smaller-N baselines.
- State whether the section is a sanity check, negative control, ablation,
  capacity check, robustness check, scaling check, or stress test.

### Setting Block

Write the setting before the table:

- task or dataset;
- target distribution or target behavior;
- changed variables;
- fixed controls: steps, batch size, optimizer, learning rate, seeds,
  evaluation samples, decoding/sampling budget, and major hyperparameters;
- fairness caveats;
- intended reading: what rows or columns should be compared.

### Result Table

Put identity columns before metrics:

- method or training objective;
- model, architecture, or backbone;
- conditioning, prompting, feature set, dataset split, or other mode;
- parameter count, model size, or another capacity proxy when relevant.

Then add section-specific variable columns:

- task or difficulty for scaling checks;
- variant for ablations;
- batch size, data size, or sample budget for robustness checks;
- baseline/follow-up label for paired comparisons.

Only then add metric columns.

Avoid using internal labels such as `none-small`, `big`, `series`, `v2`, or
`ours-lite` as the only identity. If labels remain useful, define them nearby
and still show the real method/model/setting.

## Baseline Pairing

If a conclusion depends on relative change, put the baseline in the same table.

Examples:

- A scaling experiment should show each baseline task and harder task together.
- A capacity check should show small, parameter-matched, and conditioned
  variants together.
- A new method comparison should include the old method or negative control.
- A batch-size follow-up should join the batch-size sweep, not live alone.

Do not force the reader to jump between unrelated sections to verify the key
claim.

## New Experiments Belong On Existing Axes

Do not default to creating a new standalone section for every new experiment.
First identify which comparison axis the experiment changes, then merge it back
into the section that already owns that axis.

If the new experiment uses a different budget, still keep it on the relevant
axis when that is the meaningful comparison, but add explicit budget columns
such as steps, batch size, evaluation samples, decoding steps, sampling steps,
or ODE steps. Narrow the conclusion to the tested budget.

## Metric Discipline

Define every metric the first time it appears. Separate:

- validity or legality: does the output satisfy hard constraints?
- distribution match: does the output match the full target distribution?
- correlation or structure: did the model learn the intended dependency?
- stability: how much do results vary across seeds or subsets?
- cost: parameters, training compute, sampling steps, latency, or data budget.

Warn when a metric is insufficient by itself. For example, accuracy can be high
under mode collapse, so a report may also need distribution distance, per-mode
rates, calibration, or diversity metrics.

## Conclusion Pattern

Write each conclusion as:

1. Observation: what the table shows.
2. Interpretation: what mechanism or limitation it suggests.
3. Boundary: what the experiment does not show.

Example:

```text
The independent-token model still produces many illegal mixed patterns on the
harder all-same task. Combined with its failure on the easier task, this points
to a structural limitation from missing token communication, not a one-off
training failure. This does not prove that all MLPs fail, because the global
MLP can succeed when it sees all tokens jointly.
```

## Report Anti-patterns

Avoid:

- orphan tables with no setting;
- standalone follow-up sections whose results only make sense on an existing
  comparison axis;
- experiment names that hide the real comparison;
- conclusions based on a table in another section without pairing the baseline;
- unexplained abbreviations or internal run labels;
- reporting only the metric that makes a method look best;
- saying "method A is better" when the experiment only tested one task, one
  seed range, one budget, or one capacity regime;
- burying caveats in code comments or config files instead of the report.
