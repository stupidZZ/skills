# Ownership Lessons

Use these examples when workflow, role and capability boundaries are ambiguous.
They are distilled decisions, not a directory template.

An independent workflow owns a goal, decisions, artifacts and a lifecycle.
Training-time validation can belong to training because it owns reference data,
feedback and checkpoint selection within that lifecycle. A frozen baseline may
instead be a role in offline evaluation. Analysis and reward execution can be
shared capabilities when they retain the same semantics across consumers.
Calling something diagnostics does not establish an independent owner.

Move the concept consistently through source, active configuration catalog,
CLI, tests, documents and generated views. Consistency means matching ownership,
not identical directory trees. A small project with one configuration entry
does not need another hierarchy for symmetry.

Schema (types/defaults), loading (I/O and output binding) and validation
(cross-field invariants) can have different reasons to change. Split them only
when that distinction helps, and preserve a meaningful public facade. A huge
trainer should not absorb independent state machines just to reduce file count;
thin forwarding files should not survive without consumers.

Repeated questions about two names or layers are evidence to revisit ownership,
not a request for longer justification. List the actual unresolved boundaries
once, resolve the smallest architectural claim, and stop when real consumers
are served. A commands -> workflows -> capabilities dependency direction is one
possible outcome, not a required universal architecture.
