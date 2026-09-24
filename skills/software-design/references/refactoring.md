# Domain-Owned Refactoring

For examples distinguishing workflows, roles and shared capabilities, read
[ownership lessons](ownership-lessons.md) when those boundaries are unclear.

Use this skill when architecture work risks becoming directory rearrangement,
generic cleanup, or premature extraction into `core`, `common`, or `utils`.
The goal is to make ownership, lifecycle, semantics, and dependency direction
explicit before moving code.

## Core Rule

Refactor around the stable forces of the system:

```text
products -> ownership -> domain semantics -> lifecycle -> shared capabilities
```

Do not promote code to a shared layer just because it is duplicated, uses the
same SDK, or looks technically similar. Shared abstractions need real consumers
and the same domain meaning.

## When To Use

Use this skill for:

- splitting or merging packages, apps, services, trainers, evaluators, CLIs, or
  pipelines;
- reviewing a proposed `core`, `common`, `shared`, or `utils` extraction;
- reducing cross-application imports or unclear dependency direction;
- removing compatibility layers, forwarding modules, or unused distribution
  surfaces;
- deciding whether a refactor is complete enough to stop.

Do not use it for tiny local cleanups that do not change ownership,
dependencies, or public contracts.

## Workflow

### 0. Read Local Context First

Before proposing structure, inspect the repo's own truth sources:

- `AGENTS.md`, README, architecture docs, design notes, and package docs;
- entrypoints, CLIs, services, scheduled jobs, scripts, and build targets;
- tests and fixtures that define current behavior;
- current imports between apps, packages, and shared layers;
- active runs, artifacts, reports, or compatibility consumers that must not be
  broken.

Current user instructions and project docs override this skill.

### 1. State The Refactor Hypothesis

Write the refactor as a falsifiable hypothesis:

```text
Problem: which boundary or ownership rule is unclear?
Change: what ownership or dependency rule will change?
Invariants: which behavior, config, artifacts, or public APIs must remain?
Non-goals: what this pass will not solve?
Evidence: what tests, graphs, or end-to-end paths will prove the change?
Stop condition: when should global structure work pause?
```

If the hypothesis is only "clean up the codebase", narrow it before editing.

### 2. Map Products, Owners, And Lifecycles

Build a compact map before moving code:

- products: independently runnable or deliverable applications;
- entrypoints: CLIs, services, jobs, notebooks, scripts, APIs;
- lifecycle owners: who creates, mutates, persists, and finalizes each object;
- domain terms: words whose meaning must stay stable across the system;
- dependency direction: which layers may import which other layers;
- long-lived state: configs, checkpoints, reports, caches, runs, queues, or
  external resources.

Prefer a one-page target map over a large speculative tree.

### 3. Apply The Shared-Core Admission Gate

Before putting code in a shared layer, check whether it satisfies most of:

- at least two real current consumers need it;
- consumers need the same domain semantics, not only the same provider or SDK;
- defaults, error semantics, units, and numeric behavior should remain aligned;
- the interface can be named without application-specific vocabulary;
- the shared layer does not import concrete applications;
- extraction reduces semantic drift more than it adds adapters or wiring;
- removing one consumer would not make the abstraction meaningless.

When the gate fails, keep the code inside the owning product. A little
duplication is cheaper than a wrong shared abstraction.

### 4. Run The One-Sentence Boundary Test

For every new package, module, class, and public method, write one sentence:

```text
This owns <domain responsibility>, receives <input>, returns <output>, is used
by <consumers>, and does not own <explicit non-responsibility>.
```

If the sentence depends on vague words such as "common", "helper", "manager",
"handler", "utils", or "processing", the boundary probably needs more work.

### 5. Protect Behavior Before Moving Code

Add characterization before broad migration:

- smoke tests for public CLIs and entrypoints;
- fixtures for representative configs, data, and artifacts;
- focused tests for core numeric or contract behavior;
- import-direction checks when dependency direction is the invariant;
- snapshots or generated outputs only when they are stable enough to review.

Avoid tests that freeze incidental file layout unless layout is the real
contract.

### 6. Migrate In Vertical Slices

Prefer one complete consumer path at a time:

```text
name the domain concept
  -> define the smallest contract
  -> migrate one real consumer
  -> verify behavior
  -> migrate the next consumer
  -> delete forwarding or compatibility layers once consumers are gone
```

Do not create a full empty architecture and then fill it later. Empty packages
and permanent forwarding modules make the system look organized while keeping
ownership ambiguous.

### 7. Add Architecture Fitness Tests

Good long-term constraints include:

- apps do not import other apps' internals;
- shared packages do not import applications;
- runtime code does not import reports, runs, or historical artifacts;
- confirmed-unused distribution targets, wheels, or compatibility modules do
  not reappear;
- domain-specific packages own their own lifecycle objects.

Be cautious about tests that lock a class to one file name, require an exact
directory count, or enforce visual symmetry. Fitness tests should protect
ownership and dependency direction.

### 8. Stop Global Refactoring Deliberately

Default to stopping when most of these are true:

- a new contributor can draw the product and dependency map;
- every shared package has real consumers and a clear semantic reason to exist;
- public APIs express units, cardinality, and capability;
- the top-level lifecycle reads in order;
- compatibility layers have consumers or are deleted;
- real workflows pass through the new structure;
- remaining discomfort is mostly aesthetic rather than tied to change cost.

After that, let real feature work, operations, evaluation, or user feedback
create the next refactor pressure.

## Review Mode

When reviewing an existing refactor, lead with risks:

- behavior changed without an explicit invariant;
- a shared layer has no real consumers;
- an application imports another application's internals;
- naming hides cardinality, units, lifecycle, or ownership;
- a compatibility layer lacks a deletion condition;
- tests freeze incidental layout while missing dependency direction;
- the refactor keeps expanding after the original hypothesis is satisfied.

Then propose the smallest corrective slice.

## Output Shapes

For a refactor plan:

```text
Refactor hypothesis
Current product and ownership map
Target dependency rules
Protected invariants
Vertical migration slices
Fitness tests
Stop condition
```

For a review:

```text
Findings
Ownership or dependency risk
Missing invariant or test
Suggested correction
Residual risk
```

For implementation work, make small commits around one architectural claim at a
time, and keep pure moves separate from semantic behavior changes where
practical.
