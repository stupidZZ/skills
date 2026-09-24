# Contract-First Domain Design

Produce a small executable user workflow and contracts that let an independent
consumer complete it without reading service internals. Inspect the product's
existing requirements, contracts and owning systems first. For migration of
existing architecture, also read [refactoring](refactoring.md) when needed.
Contract decisions apply to both new systems and changes to existing systems.

## Workflow

1. Write the shortest loop: input provider -> stored fact -> action owner ->
   successful fact -> user verification. State non-goals.
2. Admit an object only if it has a real identity, independent consumer need,
   lifecycle and invariants. Test whether users need to create, find, reference,
   authorize or delete it independently. Derived statistics, transport files,
   caches and one-off actions often do not qualify.
3. Assign each fact and lifecycle to its owner. A result registry need not own
   execution, queues or retries. Missing results can be a query rather than a
   stored completion record. Do not introduce entity/version layers merely to
   preserve history; require version browsing, rollback or another consumer.
4. Stabilize identity, relations, invariants and frequently queried fields.
   Put extensibility behind explicitly owned slots. Do not recursively guess
   resource references or permissions inside arbitrary JSON. Keep stable resource
   identity separate from temporary URLs and credentials.
5. Define failure behavior before choosing implementation. Use
   [contract checks](contract-checks.md) for retry timelines, evidence
   ownership and capability probes.
6. Make positive, negative and concurrent examples executable against schemas
   and semantic validators. State assumptions not yet verified.

Revisit and remove contracts that cease to serve the loop. Immutability is a
design choice: where results depend on a definition's semantics, record a new
definition when model, preprocessing or interpretation changes instead of
silently reinterpreting old results.

## Deliverable And Stop Condition

Deliver the user loop, admitted/rejected objects with reasons, ownership and
lifecycle boundaries, machine contracts, failure examples and outstanding
capability checks. Use the project's existing artifacts rather than creating
a separate document for every item. Stop when a consumer can implement the
requested loop and the remaining uncertainties are explicit; do not design
future object families solely for hypothetical expansion.
