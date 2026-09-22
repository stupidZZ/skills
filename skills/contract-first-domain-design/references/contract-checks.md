# Contract Checks

## One Owner Per Fact

| Fact | Typical owning artifact |
| --- | --- |
| Goal, user loop, non-goals | Product brief |
| Objects, relations, lifecycles, invariants | Domain document |
| Rationale and reconsideration trigger | ADR |
| HTTP paths, authentication, status and messages | OpenAPI |
| Fields, types, enums, local constraints | JSON Schema |
| Cross-field and cross-object constraints | Semantic validator |
| Positive/negative examples and error semantics | Fixtures and tests |

Documentation must not independently invent a second field contract. Generated
reading views derive from the owning sources. Happy-path examples alone leave
failure policy to consumer guesswork.

## Idempotency Timelines

Define fact identity first, then examine:

- Same identity and same payload: successful no-op, replacement or another
  explicitly chosen behavior?
- Same identity and different payload: conflict or permitted revision?
- Response lost after commit: how does a client establish completion?
- Batch partly succeeds: which items can be retried independently?
- Concurrent requests: where is uniqueness enforced atomically?
- Timeout or external dependency failure: which component owns retries?

An idempotency header alone answers none of these. A registry might store only
immutable completed results while the execution system owns pending, failed,
skipped and retry states. That choice is valid only if it fits the user loop.

## Capability Evidence

For each correctness-critical external capability, record target version and
environment, a minimal probe, positive/negative/concurrent/failure cases,
latency or capacity limits when relevant, and a fallback if the probe fails.
Service names do not prove transaction or authorization guarantees. Run probes
within the current authorization and cost scope.

Distinguish coherent contracts, observed platform capabilities, implemented
business behavior, test evidence and deployed operation. One does not imply
the others. Keep live readiness and deployment state in the owning project.

## Overmodeling Counterexample

A result hub initially proposed snapshots, sealed result sets, binding objects
and run queues. A fixed-input first version needed only immutable datasets,
external execution and individual results. Coverage was derived; credentials
were client configuration; a result grouping had no independent lifecycle.
The lesson is the admission test, not a universal ban on those concepts.
