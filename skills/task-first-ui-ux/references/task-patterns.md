# Task Patterns And Counterexamples

| Task | Useful starting structure | Common failure |
| --- | --- | --- |
| Compare identical metrics across objects | Compact aligned table | Full-width cards separate values from identities |
| Express order, ties and uncertainty | Ranking board with tied and undecided regions | Numeric fields require mental reconstruction |
| Understand differing judgments | Paired orders and explicit disagreements | Arrow strings and long prose hide the difference |
| Browse many media items | Continuous browsing with stable order and progressive loading | Each item requires a separate selection |
| Explore relationships | Named chart plus inspectable numeric evidence | IDs only; projected distance passed off as exact similarity |
| Switch functions in one workspace | Stable tabs | Accumulating navigation links and primary buttons |

Cards remain useful for a few heterogeneous objects and independent content.
Do not flatten different user tasks into one visual pattern.

Overview should put identity, key metrics, comparison scope and insufficient-data
state together. Detail should lead with a conclusion and the comparison that
explains it; use evidence tables instead of repeated metric sentences. Editing
should allow move, tie, remove and cancel operations where those express intent.
After apply, collapse the editor only if its summary and re-edit action remain.

Use names for identity, short IDs only to disambiguate. Separate group color,
selection marks and focus opacity without making text unreadable. Move labels
or add leader lines rather than silently moving data points. Display ties in
human terms even if the statistical algorithm uses fractional ranks. Raw model
scales may be incomparable; inspect normalization assumptions.

Wide tables need fixed identity columns and headers in the correct scroll
container. Expanding details changes the scroll geometry. Do not force equal
card heights or let a sidebar unnecessarily constrain the full list below it.

These patterns were distilled from repeated data-workbench revisions: adding
explanatory text did not fix a structure unsuited to comparison; separately
patching each selection entry created inconsistent state; replacing a drag
implementation fixed a symptom without proving its original failure mechanism.
They are reusable observations, not a mandate for a particular framework.
