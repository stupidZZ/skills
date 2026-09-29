# Deprecated: skills are maintained in zz-wiki

The single source for these skills is now [world-sim-dev/zz-wiki](https://github.com/world-sim-dev/zz-wiki),
under its `skills/` directory. Submit skill improvements there; do not maintain or synchronize a second
copy in this repository. Existing code and Git history remain here for historical reference.

[The migration PR](https://github.com/world-sim-dev/zz-wiki/pull/8) includes the training-settings
improvements originally proposed in this repository's PR #3. Complete that migration before replacing
an existing installation; source changes do not automatically update installed plugins.

Active plugin skills are maintained in [zz-wiki/skills](https://github.com/world-sim-dev/zz-wiki/tree/main/skills).
The old Kian-specific `feishu-task-sync` and template are not migrated to zz-wiki.
Their historical sources remain in this retired repository; no legacy mirror or activation is introduced.

No repository history is deleted, and this change does not alter repository visibility or uninstall
existing skills. Follow the destination repository's installation and migration instructions.
