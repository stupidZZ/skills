# Reading View And Change Review

Use only for a documentation website or generated reading interface. Plain
Markdown edits do not require this workflow. Current-update batches and badges
below apply when readers need editorial change review; do not add that feature
to every documentation site.

A possible small-project layout is Markdown under docs, a curated manifest,
a deterministic generator with build/check modes and a local UTF-8 preview.
Existing documentation frameworks can fulfill the same roles. Physical
centralization of docs does not transfer ownership away from their domains.

## Current Review Batch

Model a batch as date and title, and a changed page as status (new or updated),
one-sentence reason to reread and highlight anchors. Give changed sections
their own anchor and status. An updated old page may contain a new section;
do not apply the page status to every heading.

Expose the changes throughout the reader's path:

1. A distinct current-update area with date, title and changed page links.
2. Status badges on those pages in ordinary navigation too.
3. Page-top status, date, batch title, summary and readable highlight links.
4. Section-specific status on highlight links, the local table of contents and
   the affected headings.

This is editorial selection, not a mechanical Git diff. Formatting churn need
not demand attention; a tiny change to a conclusion may deserve a highlight.
On the next batch, replace old markers everywhere. Validate enums, required
summaries, anchor existence and duplicate section entries. Test visible reading
entry points, not just metadata presence.

A real review found that an update list plus page summary technically met a
two-location requirement but still hid changes from readers using the ordinary
navigation. Another required distinguishing new sections within existing pages.
These observations motivate the linked page/section design, not a mandated CSS
palette, card radius or application framework.

## Usability And Encoding

- Present one document at a time with grouped navigation and an in-page index.
- Provide search, copyable deep links and correctly rewritten Markdown links.
- Check readable text width, mixed-language fonts, line height and tables.
- Serve the actual wiki at the root URL rather than a directory listing.
- Use UTF-8 consistently in files, HTML charset and HTTP headers; if a BOM is
  used, ensure the parser handles it. A BOM is not universally required.
- When offline reading is required, avoid runtime CDN or backend dependencies.

Compare a supplied reference's navigation, search, update area, page summaries,
section entry points, typography and tables. Functional parity alone is not
reference fidelity. Exercise navigation, anchors, copyable links, search with
empty results, history/back and narrow layouts in the actual browser.

Structural tests prove consistency; browser evidence proves that cues are
visible and interactions work. In an Orca environment, check for the orca CLI
and read its current browser skill before concluding no browser exists. Tool
availability is environment-specific and is not a requirement to install Orca.
