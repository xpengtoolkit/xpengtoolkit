# Agent notes

## Publishing identity - read this before pushing anything

Everything that leaves this machine is published as **xpengtoolkit**, never as the
maintainer's personal account. Commits are already configured locally as
`xpengtoolkit <xpengtoolkit@users.noreply.github.com>`; the risk is **authentication**,
because `gh` and the macOS keychain on the maintainer's Mac can belong to the personal
account instead.

Before any `git push`, tag or `gh release`, all of these must say `xpengtoolkit`:

```bash
git log -1 --format='%an <%ae> %cn <%ce>'
gh auth status
printf "protocol=https\nhost=github.com\n\n" | git credential fill | grep '^username='
```

If any of them does not, stop and ask. Never publish with a personal account or token
"just this once" - a release is publicly attributed to its creator. The full rule is
in [`.cursor/rules/publish-identity.mdc`](.cursor/rules/publish-identity.mdc).

## Conventions

- Tests are plain scripts, no pytest: `python3 tests/test_config_flow.py`. Some files
  also carry `unittest` classes, so `python3 -m unittest discover -s tests` runs those.
- Credentials never enter git: `custom_components/xpeng/certs/*.pem`,
  `xpeng_tokens.json` and `xpeng_tenant.json` are ignored. Check
  `git diff --cached --name-only` before committing.
- Releases: bump `custom_components/xpeng/manifest.json`, commit with a `0.6.x:` title,
  tag `0.6.x` — bare, no `v`: the `v`-prefixed tags stop at `v0.6.24`, so the bare
  form has been the convention since 0.6.25 — then
  `gh release create 0.6.x --title "<the commit title>" --notes-file <file>`. The
  companion app is versioned separately in `xpeng_renew/config.yaml`; a push touching
  `xpeng_renew/**` rebuilds its GHCR image automatically (`Renewal image` workflow).

  On this exFAT workspace `git add -A` occasionally misses the file you just wrote
  (git's stat cache cannot see the coarse mtime change). Re-run
  `git add custom_components/xpeng/manifest.json` explicitly and check
  `git show HEAD:custom_components/xpeng/manifest.json | grep version` before
  tagging - a release whose manifest still says the previous version makes HACS
  offer the update forever.
- Translations: `translations/en.json` is the runtime source of truth and
  `strings.json` the reviewed copy; a test keeps them identical. Every other
  `translations/<lang>.json` must carry the same 343 keys - run
  `python3 tools/check_translations.py` after touching any of them, and
  `python3 tests/test_translations.py` (also part of the normal suite). Rules and
  the language list: `docs/TRANSLATIONS.md`.
- Entity names: set a translation key, never `_attr_name` (it wins over the
  translation and freezes the name in English). Entity ids are pinned to the
  English name by `XPengEntity.suggested_object_id` via `entity_names.py`, so a
  translated name must never change an id; do not add a placeholder to an
  English entity name.

<!-- dsh-context-pointer -->
## Workspace context — read before exploring

Cross-machine context for this workspace: `/Volumes/projects/Work/.harness/context/Volumes-projects-xpeng-mqtt-public.md`
(purpose, layout, commands, current state, open threads, gotchas). Read it first;
it is maintained by the `dsh-context` skill. Update its **Current state** and
**Open threads** before you finish a session that changed anything, and bump the
`generated:` date. Stale context is worse than none.
