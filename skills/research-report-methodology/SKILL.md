---
name: research-report-methodology
description: |
  Research and experiment report methodology skill. Use when the user asks an
  agent to design, write, review, revise, or consolidate a research report,
  experiment report, benchmark report, ablation report, or empirical analysis.
  The skill emphasizes self-contained experimental settings, fair comparisons,
  baseline pairing, placing new experiments on the right comparison axis,
  interpretable tables, metric definitions, and bounded conclusions.
metadata:
  version: 0.1.0
  homepage: https://github.com/stupidZZ/skills/tree/main/skills/research-report-methodology
  tags:
    - research
    - reporting
    - experiments
    - methodology
---

# Research Report Methodology Skill

Use this skill to help write or review empirical research reports. The goal is
to prevent reports from becoming disconnected result tables. A reader should be
able to understand what each experiment asks, how it was run, what it compares
against, what the metrics mean, and what conclusions are or are not supported.

## Core Rule

Make every experiment section self-contained.

For each section, ensure the report answers:

1. What question does this experiment answer?
2. What is the task and target distribution or evaluation target?
3. Which variables change, and which controls stay fixed?
4. What method, model/backbone, conditioning, parameter count, dataset, and
   training/evaluation budget does each row use?
5. Should the table be read as a ranking, an ablation, a paired baseline
   comparison, a scaling check, or a stress test?
6. Which metrics support the conclusion, and which metrics are only auxiliary?
7. What can this experiment prove by itself, and what requires comparison with
   another experiment?

## Recommended Report Structure

Organize reports in this order unless the user provides a stronger structure:

1. Questions: state the overall research questions before showing results.
2. Common protocol: define shared task setup, data generation, training budget,
   evaluation budget, seeds, and core metrics.
3. Glossary: define repeated terms, abbreviations, and internal labels.
4. Experiment sections: include setting, comparison design, result table, and
   analysis for each experiment.
5. Takeaways: include only conclusions supported by earlier tables.

## Section Template

Each experiment section should have three layers.

### 1. Header

- Name the experiment and the comparison logic.
- Prefer "N=2 vs N=4 scaling check" over "N=4 results" when the result only
  becomes meaningful against a baseline.
- State if the experiment is a sanity check, negative control, ablation,
  capacity check, robustness check, or scaling check.

### 2. Setting Block

Write the setting before the table. Include:

- Task or dataset.
- Target distribution or target behavior.
- Changed variables.
- Fixed controls: training steps, batch size, optimizer, learning rate, seeds,
  evaluation samples, decoding/sampling budget, and major hyperparameters.
- Fairness caveats: anything that is not fully matched and why.
- Intended reading: what columns should be compared.

### 3. Result Table

Every result table should include explicit identity columns before metrics.
Use the names appropriate for the domain, but include the same information:

- method or training objective;
- model, architecture, or backbone;
- conditioning, prompting, feature set, dataset split, or other important mode;
- parameter count, model size, or another capacity proxy when relevant.

Then add the section-specific variable column:

- task or difficulty level for scaling checks;
- variant for ablations;
- batch size, data size, or sample budget for robustness checks;
- baseline/follow-up label for paired comparisons.

Only then add metric columns.

Avoid using internal labels as the only identity. Labels such as `none-small`,
`big`, `series`, `v2`, or `ours-lite` are acceptable only if the table also
shows the real method/model/setting and the labels are defined nearby.

## Baseline Pairing

If a conclusion depends on relative change, put the baseline in the same table.

Examples:

- A scaling experiment should show each baseline task and harder task together.
- A capacity check should show small, parameter-matched, and conditioned
  variants together.
- A new method comparison should include the old method or negative control in
  the same comparison surface.

Do not force the reader to jump between unrelated sections to verify the key
claim.

## Place New Experiments on the Right Axis

Do not default to creating a new standalone section for every new experiment.
First identify which comparison axis the experiment changes, then merge it back
into the section that already owns that axis.

Examples:

- If only batch size changes, add the result to the batch-size comparison table.
- If task length, difficulty, or scale changes, add the result to the scaling
  table with the easier baselines.
- If a new method is added, add it to the method comparison table with the old
  method or negative control.
- If a new backbone is added, add it to the backbone table under the same
  method, conditioning, and budget when possible.

If the new experiment uses a different budget, still keep it on the relevant
axis when that is the meaningful comparison, but add explicit budget columns
such as steps, batch size, evaluation samples, decoding steps, sampling steps,
or ODE steps. Narrow the conclusion to the budget that was actually tested.

## Metric Discipline

Define every metric the first time it appears. Separate different kinds of
success.

Typical categories:

- Validity or legality: does the output satisfy hard constraints?
- Distribution match: does the output match the full target distribution, not
  just the valid set?
- Correlation or structure: did the model learn the intended dependency?
- Stability: how much do results vary across seeds or subsets?
- Cost: parameters, training compute, sampling steps, latency, or data budget.

Warn when a metric is insufficient by itself. For example, accuracy can be high
even under mode collapse, so a report may also need distribution distance,
per-mode rates, calibration, or diversity metrics.

## Fairness Checklist

Before writing conclusions, check whether the comparison controls:

- task and target distribution;
- train/eval data or sampling process;
- training steps;
- batch size, unless batch size is the variable;
- optimizer, learning rate, and weight decay;
- evaluation sample count;
- random seeds;
- decoding, sampling, or integration budget;
- parameter count or model capacity, when the claim involves architecture or
  conditioning rather than size.

If a control cannot be matched, say so directly and narrow the conclusion.

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

## Revision Workflow

When revising a research report:

1. Identify each section's claim.
2. Place every new experiment on the existing comparison axis it modifies.
3. Check whether the section has the baseline needed to support that claim.
4. Redesign the table so identity columns come first, variable columns second,
   and metrics last.
5. Add budget columns when controls are not fully matched.
6. Add or tighten the setting block.
7. Add glossary entries for unclear terms and internal labels.
8. Rewrite conclusions to include observation, interpretation, and boundary.
9. Validate that the report is readable without opening source configs,
   scripts, or data files.
10. If the report is generated by code, run a render or row/column count check.

## Anti-patterns

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
