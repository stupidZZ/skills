---
name: project-wiki-maintenance
description: Build or maintain a project's documentation wiki from canonical source documents, with curated navigation, change highlights and verified generated reading views. Use for documentation generators, manifests, stale outputs, wiki navigation or reviewing a supplied documentation UI reference.
metadata:
  version: 0.1.0
---

# Project Wiki Maintenance

Preserve one source for each fact and make its reading view useful to people.
Read the existing docs, generator, build commands and project instructions
before selecting a toolchain. Keep an adequate existing documentation site;
small one-off tasks need no generator. Source organization should reflect
content ownership without mechanically reproducing the code tree.

## Maintenance Loop

1. Update the owning Markdown or structured contract. Never edit generated HTML
   as a separate body. API field facts stay in their machine contract.
2. Curate the pages that guide current work through a manifest or the existing
   site's equivalent. Historical reports are not automatically current merely
   because they are stored under docs.
3. Describe this review batch and the pages/sections worth rereading using
   [reading-view requirements](references/reading-view.md).
4. Rebuild the derived view with deterministic output. Exclude timestamps,
   absolute machine paths, random IDs and unstable ordering.
5. Check stale output, links, source existence, navigation, heading anchors and
   encoding using the existing build/check mechanism.
6. Preview the generated result in an actual browser and exercise update links,
   normal navigation, section jumps, search including empty results, history/back
   and narrow layouts. Use the available browser tooling before installing more.

If the user supplies a reference site or screenshot, list its observable
structure and compare the result against it: navigation, search, update area,
page summary, section entry points, typography and tables. Functional parity
alone does not establish reference fidelity.

## Delivery

Report changed source documents, generated output, performed checks and a usable
preview or artifact link. Distinguish generator tests from visual and interaction
verification. If browser checks are unavailable, state that gap explicitly.

For modest offline wikis, committing a deterministic HTML artifact can simplify
review and use across machines. Existing hosted pipelines or large outputs can
build only in CI. Do not require committed HTML, a specific manifest format or
an apps/packages directory tree for every project.
