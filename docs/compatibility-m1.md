# M1 Configuration and Diagnosis Report

Tested on September 14, 2026 (America/Chicago).

## Scope and result

M1 adds a private cross-host home, validated profiles, SQLite schema version 1, existing-library bindings, a shell-free read-only Lark adapter and a diagnostic skill. It does not add papers, create remote resources, read papers or publish notes.

| Component | Tested version / result |
| --- | --- |
| OS | Windows |
| Python | 3.13.5 |
| Claude Code | 2.1.270; session-local plugin load passed |
| Codex CLI | 0.154.0-alpha.6.2; local marketplace install passed |
| lark-cli | 1.0.89; existing user identity verified |
| Paper2Lark | 0.2.0 |
| Shared runtime SHA-256 | `c5cc0c6bc88983b1cf10988d26efd5ead8c05fe2ea9783c9e9db2b94874557fb` |

Claude Code and Codex each loaded their generated diagnostic skill and executed `doctor --offline` plus `probe` from the installed package. Both returned the runtime hash above. The deliberately absent test homes remained absent, proving that offline diagnosis did not initialize configuration, SQLite or bindings.

## Existing-library validation

The original user's current paper library was inspected using read-only CLI operations and bound inside an ignored, isolated test home. No production Wiki, document, Base field, view or record was changed.

The binding resolved the Wiki parent, physical Base/table and template document; mapped all 12 logical index fields by stable field ID; found no fields missing from the future add/read workflow; captured status mappings; and stored a physical-library ID and account fingerprint locally. A subsequent `doctor --verify` reported the state, account, Wiki parent, table, mapped schema, status options and template as accessible.

SHA-256 snapshots of every file in the isolated home were identical before and after doctor. This verifies the tested diagnosis path did not rewrite the config, SQLite database or binding. Raw responses and private resource identifiers remain under ignored `.paper2lark-work/` evidence and must not be published.

The first live binding attempt observed credentials needing refresh. `lark-cli auth status --json --verify` refreshed and verified the existing login. Paper2Lark did not run `auth login`, `auth logout` or start an OAuth flow. A sandbox-only `token_missing` remains classified as credential availability unknown until compared with a normal execution context.

## Deterministic validation

The final automated run collected 53 tests: 52 passed and one directory-symlink test was skipped because this Windows account cannot create symbolic links. The corresponding Windows junction case was tested separately in M0: redirected build output was rejected before writing and the target directory remained empty.

Covered behavior includes:

- configuration precedence, explicit environment files, safe profile/home names, policy caps and complete resource overrides;
- no-overwrite config initialization and no-create configuration reads;
- SQLite initialization, v0 backup, schema validation, rollback on conflicting legacy metadata and rejection of future schemas;
- 180 concurrent initialization calls across fresh homes;
- read-only inspection that refuses active WAL and rollback-journal states without creating SHM or other files;
- native executable resolution, shell-free argv, timeout handling and a strict read-operation allowlist;
- distinct diagnoses for credential-store access, unavailable sandbox credentials, explicit login absence, token refresh, network errors, missing scope, permission errors and account mismatch;
- malformed nested provider objects and explicit verification failures becoming stable diagnostics instead of Python crashes;
- field-ID mappings surviving renames while deletions/type changes stop with `SCHEMA_DRIFT`;
- atomic binding writes and compare-before-replace behavior so a stale host cannot overwrite another host's newer local binding;
- both host packages running from unrelated paths with spaces and Chinese characters, checksum enforcement and reproducible runtime bytes.

Both generated plugin manifests passed their installed validators. Public source, documentation and distribution artifacts were scanned for known private account/resource identifiers; none are expected outside ignored evidence.

## Safety boundaries

The M1 Lark runner has an explicit allowlist containing only auth status and resource-read operations. It appends `--as user` itself and uses `subprocess` with `shell=False`; supplied paper titles or tokens are never command strings. Provider error bodies are used for classification but are not returned or persisted by the runtime.

Binding is a local write, not remote authorization. A different account or physical target needs explicit replacement. A resource override must include Wiki, Base, table and template together. An index URL's view is retained only for display; field inspection addresses the physical table and is not filtered by that view.

SQLite immutable reads can ignore uncommitted journals, so doctor first and last checks the main database and refuses nonempty WAL or rollback journals. It does not delete these files. Initialization uses SQLite's exclusive transaction and validates the resulting schema before commit.

M1 does not solve Lark document concurrency. The M0 finding still applies: the tested CLI accepted a stale revision for an append. Future publishing must keep in-place replacement disabled until a true atomic primitive is established.

## Known support boundary

Only the exact Windows/host/Python/CLI versions above are certified. POSIX file modes are tested in code but Linux and macOS hosts have not been exercised. Windows uses inherited ACLs, so users should choose a private `PAPER2LARK_HOME`.

The following remain unimplemented: collection and deduplication, live keyword selection, paper acquisition/extraction, model handoff, note generation, publishing/recovery, new-library provisioning and schema migration. A healthy doctor result establishes configuration and access only.

## Reproduction

From the repository root:

```powershell
python -m unittest discover -s tests -v
python scripts/build_plugins.py
python C:/Users/USER/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py ./dist/codex/plugins/paper2lark
claude plugin validate ./dist/claude/plugins/paper2lark
claude plugin validate ./dist/claude
```

The Codex validator location is installation-specific. Use the README commands to install the generated local marketplace, then ask a fresh Codex session to use `paper2lark-doctor`. Load the Claude package with `--plugin-dir` and invoke `/paper2lark:doctor`.

Live reproduction should bind only resources the user has selected. Binding and doctor are read-only remotely, but they save private identities locally. Never commit `~/.paper2lark`, `.paper2lark-work`, raw auth output or bindings.
