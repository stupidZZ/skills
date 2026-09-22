# Experimental Meaning And Counterexamples

Read this when metric reuse, configuration reuse or historical runs obscure
what an experiment actually establishes.

The same judge may provide group-relative training feedback, a held-out monitor,
evaluation against a fixed reference and a checkpoint-selection metric. Group
average pairwise wins may be structurally constrained, so they cannot simply
be plotted as absolute quality across checkpoints. A checkpoint selected by
one aggregate evaluation may still fail to outperform the base in direct paired
comparison. Report the protocol boundary rather than calling it globally best.
Pointwise monitoring is optional, not a mandatory extra layer for every study.

Repeated final values in independently reviewable paired recipes can preserve
provenance. Shared mutable inheritance can silently change the treatment when
the control changes. Frozen, fully displayed effective settings can provide
the same guarantee through another configuration mechanism. The essential
property is reconstructable differences, not a prescribed Python syntax.

Units and consumers should be visible in names: prompts per step differs from
candidates per prompt; HTTP concurrency differs from model batching and I/O
prefetch. Capability-specific types can prevent invalid backend combinations,
but validated dictionaries can be appropriate during rapid exploration.

Historical configs and runs explain evidence without becoming templates for
current code. Training, offline benchmarks, filtering and reports can share
scoring semantics while retaining different lifecycles. A structural refactor
must not quietly change reward, defaults or selection; isolate changes to
experimental meaning, not every harmless instance of code duplication.
