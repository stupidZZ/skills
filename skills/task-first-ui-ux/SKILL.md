---
name: task-first-ui-ux
description: Design, review or repair task-oriented data interfaces, comparison tables, ranking inputs, charts and interactive detail views. Use when users struggle to compare, interpret or manipulate information, or when an interaction fix needs real behavioral verification.
metadata:
  version: 0.1.0
---

# Task-First UI/UX

Start with what the user must compare, decide and do next. Inspect the current
interface, real data, project conventions and any supplied visual reference.
Keep the scope proportional: a broken drag interaction rarely needs a redesign.
This skill focuses on information structure and interaction evidence; respect
the project's visual system and accessibility requirements.

## Design The Task Surface

1. Identify one primary user journey and the decisions it supports.
2. Choose the information structure for that journey using
   [task patterns](references/task-patterns.md). These are defaults, not universal
   layout requirements.
3. Design overview, detail and editing together: overview selects candidates;
   detail explains a judgment; editing expresses intent.
4. Map shared state across search, charts, lists and detail panels. Distinguish
   locating, hovering and selecting. Define draft versus applied state and
   label stale results after edits. Do not silently replace selected objects.
5. Check data semantics separately from presentation: missing is not zero;
   display rounding does not define ties; projection distance is not an exact
   correlation; clustered objects need not satisfy every pairwise constraint.
6. Exercise long names, ties, missing values, empty results, dense data, uneven
   groups, narrow screens and expanded details before declaring the view done.

Expose filter scope and counts with an accessible way to see all evidence.
An empty disagreement filter should say there are no disagreements, not switch
filters silently. Navigation changes views; commands act; controls set conditions.
Default values require a domain rule, not an arbitrary selection order.

## Investigate Failed Interactions

If the user reports the same issue again, treat the earlier repair as unverified.
Confirm the running environment and asset version, then reproduce the failure.
Separate not-triggered, cancelled, target-missed, not-committed and overwritten
state transitions. For dragging inspect press, activation threshold, movement,
hit testing, release, cancellation, focus loss, scrolling and remounts.

Use the same reproduction before and after the fix. A button or keyboard
alternative is useful but does not prove dragging works. Choose native drag,
pointer events or a library based on event evidence and input-device needs;
one successful replacement does not establish the old implementation's cause.
If observation is unavailable, state the missing evidence and label a bounded
fix as unverified rather than repeatedly claiming resolution.

## Validate And Deliver

- Computation tests verify data invariants; event tests verify transitions;
  screenshots verify layout; actual interaction verifies the usable journey.
- Walk the main journey: empty -> edit -> apply -> detail -> edit again.
- Check scrolling retains identity, headers and scope; verify the actual scroll
  container and global CSS, not only the component's sticky declarations.
- Exercise core click/drag/cancel actions, keyboard alternatives and feedback.
- Report the implemented behavior, what was actually exercised and any gaps.
  Stop expanding the design once the requested journey is accepted.
