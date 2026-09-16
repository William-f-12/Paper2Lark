Use this skill when the user asks to diagnose Paper2Lark, check an existing library binding, or investigate why Lark is unavailable in an agent. It does not collect papers, publish notes, provision resources or repair configuration.

Locate this installed `SKILL.md` and resolve `../../scripts/paper2lark.py` relative to its containing directory. Invoke that absolute launcher path using Python 3.11+. Quote all paths. Global options precede the command:

```text
python <absolute-launcher> [--home <absolute-private-home>] [--profile <name>] doctor
```

Start with the normal `doctor` command for a requested Lark check. Use `doctor --offline` when the user asks for local-only inspection; this does not invoke lark-cli. Use `doctor --verify` when existing credentials need server verification or ordinary CLI token refresh. Neither mode starts a new authorization flow. Explain warnings separately from errors; an uninitialized home is not proof of a broken installation.

The command reads configuration and existing bindings without creating state, rewriting mappings or repairing anything. It explicitly uses the user identity. Never run `config init`, `state init`, `bind`, `auth login` or `auth logout` merely to make a diagnostic pass.

For `CREDENTIALS_UNAVAILABLE` or sandbox `token_missing`, credential availability is uncertain. If the same account works in a normal terminal, treat this as an execution-context discrepancy. Compare the same read-only check in the normal context when authorized and available through the host's permission mechanism; do not bypass host restrictions. If that comparison cannot run, report the limitation. Do not tell the user to reauthorize based only on this error.

`CREDENTIAL_STORE_INACCESSIBLE`, `LOGIN_REQUIRED`, `TOKEN_REFRESH_REQUIRED`, `NETWORK_ERROR`, `MISSING_SCOPE` and `ACCOUNT_MISMATCH` are distinct findings. Only an explicit signed-out result supports the missing-login diagnosis. Report account mismatch without silently changing profiles or rebinding. `STATE_INSPECTION_UNSAFE` means the database is active or cannot be inspected safely; do not delete WAL/SHM files. Schema drift requires a separately requested remapping operation.

Report the JSON findings and what was actually checked. Do not expose raw auth responses, scopes, credentials or private resource IDs in public reports. M4 index management, local reading and journaled Wiki publication are available through separate add, library and read workflows; doctor itself does not exercise them. A healthy doctor result is not proof that publication or recovery works.
