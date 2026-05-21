# Feishu permission manifest

`required-scopes.json` is the canonical list of Feishu permissions this
Skill needs. It uses the schema accepted by the Feishu open platform's
**Permission Management → Batch Import** dialog so users can paste it once
instead of toggling each scope by hand.

## How to apply it

1. Open the Feishu developer console: <https://open.feishu.cn/app>.
2. Pick your self-built app, then go to **Permission Management**
   (权限管理) in the left side bar.
3. Click the **Batch Edit / Batch Import** button (批量编辑 / 批量导入)
   near the top of the scope table.
4. Paste the entire contents of `required-scopes.json`.
5. Submit / confirm.
6. Click the banner at the top of the page that says
   **Create Version & Release** (创建版本并发布). New scopes do not take
   effect until a new version has been released and approved.

> The Skill's `bootstrap.py doctor` and `bootstrap.py install --resume`
> commands will report `missing_scopes` if any of the user-identity scopes
> below are unavailable to the authorised user. If `doctor` is green after
> the OAuth round-trip you do not need to re-import.

## Schema notes

- `scopes.tenant` — application-identity scopes. This Skill does **not**
  require any tenant-side scopes today, but the array is kept for future
  growth; leaving it empty is intentional.
- `scopes.user` — user-identity OAuth scopes. These are the ones the Skill
  asks for during OAuth (`bootstrap.py install` step 1). They must all be
  present **and the new app version must be published** before
  authorisation will succeed for an arbitrary user. (You may sometimes see
  scope coverage take effect without a published version if you are
  testing under the developer's own account, but treat that as undefined
  behaviour; always publish before sharing the Skill with another user.)

## Bumping this file

If a future Skill version needs new scopes:

1. Update `required-scopes.json` here.
2. Update `REQUIRED_USER_SCOPES` in `scripts/bootstrap.py` to match.
3. Bump the Skill `version` in `SKILL.md`.
4. Note the new scope in `CHANGELOG.md` so existing users know they have
   to re-import + re-publish + re-authorise.
