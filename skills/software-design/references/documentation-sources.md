# Documentation Sources And Derived Views

Use for document ownership, generators or stale derived output, not ordinary
prose editing. Inspect existing Markdown, schemas, generation commands and
project conventions. An adequate existing toolchain needs no replacement;
a small Markdown-only wiki needs no generator or browser preview.

Keep one owner per fact. API fields belong in their machine contracts; prose
explains rationale and usage without inventing a second schema. Historical
reports are not current merely because they remain under docs. Organize source
by content ownership without mechanically reproducing the code tree.

When a generated reading view exists or is requested:

- Update its canonical Markdown or structured source, not generated HTML.
- Use the current site's navigation or a curated manifest to select relevant
  pages; do not mandate a particular file format or directory hierarchy.
- Rebuild deterministically, excluding incidental timestamps, local absolute
  paths and random identifiers. Deliberate editorial dates are content.
- Check stale output, links, source existence, anchors and encoding.
- Small offline wikis may commit deterministic HTML; hosted or large builds may
  run only in CI. Choose based on the project's delivery needs.

For navigation, search, change highlights or browser usability, use
`task-first-ui-ux` and its documentation-reading reference when available.
Generator tests do not establish browser usability. Report source and derived
changes, performed checks and the relevant artifact; do not claim visual
verification from source checks alone.
