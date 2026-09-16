# Upgrade and recover Paper2Lark

Plugin files are replaceable. Your private home is durable: configuration, profile bindings, SQLite state, reading runs and setup journals remain outside host caches. Keep the same absolute `PAPER2LARK_HOME` and `PAPER2LARK_PROFILE` in both hosts, or supply `--home` and `--profile` before every runtime command. Installing or removing a plugin must not remove this home or change Lark authorization.

## Upgrading from 0.7.0 to 0.7.1

Stop every 0.7.0 writer in both hosts before the first 0.7.1 write. Both hosts must use 0.7.1 together against one home: 0.7.0 cannot honor the collection intent journal added in 0.7.1. This is a same-machine coordination rule, not a cross-machine exactly-once guarantee.

The SQLite schema remains v3. Valid 0.7.0 homes and unfinished reading runs remain readable; no implicit state migration is introduced. Collection journals are separate version-one JSON artifacts under the private home. A 0.7.0 uncertain file-only creation has no journal, so inspect the bound table before retrying it.

Setup plans remain exact-version artifacts. A plan created by 0.7.0 returns `SETUP_RUNTIME_MISMATCH` under 0.7.1. Retain the original runtime and use it for that plan after confirming state compatibility; never edit the saved version or plan digest. New reading runs use corrected nested human-only template protection. Existing drafts retain their frozen template snapshot and role map.

## Before replacing a package

1. Stop active Paper2Lark operations in both hosts. Record the installed version and runtime hash from `probe` and retain the complete old extracted marketplace and original ZIP. Do not rely on a host cache: uninstalling can remove it.
2. Inspect unfinished reading runs with `runs show --run RUN-ID` and `runs resume --run RUN-ID`; inspect setup with `setup show --id SETUP-UUID`. These commands do not replay remote writes. Prefer completing ongoing operations with their original package before switching.
3. With all writers stopped, make a private backup of the entire home, including configuration, bindings, database, run artifacts and setup journals. Do not copy only a SQLite file while a writer is active. Retain remote resource references and recovery evidence privately.
4. Download and verify the new host ZIP, and extract to a new directory. Do not overlay an old extracted package. Read its compatibility report before changing host registration.

## Replace host registration

The marketplace name remains `paper2lark-local` across versions. Changing names leaves competing installations. After preserving the old package and state, replace only this marketplace registration.

In Claude Code:

```text
/plugin marketplace remove paper2lark-local
/plugin marketplace add C:/absolute/path/to/new-claude-marketplace
/plugin install paper2lark@paper2lark-local
```

Removing a Claude marketplace also uninstalls its plugins; this catalog contains Paper2Lark. Select the same intended installation scope and start a new session. A temporary `--plugin-dir` session instead switches to the new plugin directory on its next launch.

In a terminal for Codex CLI:

```powershell
codex plugin remove paper2lark@paper2lark-local
codex plugin marketplace remove paper2lark-local
codex plugin marketplace add "C:/absolute/path/to/new-codex-marketplace"
codex plugin add paper2lark@paper2lark-local
```

Start a new session. `codex plugin marketplace upgrade` refreshes Git snapshots; it does not download a newer Paper2Lark release ZIP for this local installation. These replacement commands change host registration/cache, not the private Paper2Lark home.

From the new extracted marketplace root, verify the runtime and existing home:

```powershell
python ./plugins/paper2lark/scripts/paper2lark.py --home "C:/Users/YOU/.paper2lark" --profile personal probe
python ./plugins/paper2lark/scripts/paper2lark.py --home "C:/Users/YOU/.paper2lark" --profile personal doctor --offline
```

Replace placeholders with the existing absolute home/profile. If state migration is required, review the diagnosis and run `state init` explicitly with the same options. The current runtime uses SQLite schema v3; validated v1/v2 migrations and completion of early v3 reservation tables create backups. Setup journals are separate from this schema. `config init` does not overwrite custom configuration, and a plugin upgrade does not migrate live library fields or reset language/vocabulary choices. Use a reviewed setup migration plan for intended library changes.

## Unfinished work and version compatibility

Setup plans record `runtime_version`. `setup apply` requires an exact match and returns `SETUP_RUNTIME_MISMATCH` otherwise, including when retrying a saved plan. Retain the old complete package and run its launcher against the same original home/profile to recover that plan. Never edit the version or saved plan to bypass the check. An uncertain creation requires verified explicit adoption; retrying with a different version is not a repair.

Reading manifests currently validate artifact schema, paths, sizes and hashes; they do not record an exact runtime release-version pin. SQLite and publication-plan compatibility are checked separately. This is not a promise that arbitrary future or older runtimes can resume every run. Preserve the original package, inspect the run with the new version first, and stop on compatibility errors. Use the original runtime only after checking its state-schema compatibility. `runs resume` gives read-only recovery guidance; it does not itself publish. A lost publication response must be reconciled using the saved plan, never by preparing a duplicate run.

`runs cancel` and `setup cancel` explicitly abandon local work while retaining artifacts; they do not undo remote resources. Do not cancel merely to clear an error unless abandonment is intended. Both hosts coordinate within the same machine/home; no cross-machine exactly-once guarantee exists.

## If the upgrade cannot continue

Keep both packages and all private artifacts. Diagnose with `doctor --offline`, compare configuration/home/profile, and inspect the saved run or setup journal. A sandbox credential failure is not evidence that authentication needs resetting.

Reinstalling an older plugin changes code, not remote history. Do not blindly replace the live database with an older backup: it can discard publication journals, active-run reservations and known remote IDs while leaving Lark writes in place. Restore only as a deliberate recovery procedure after reconciling local and remote state. Keep originals and seek a specific migration/recovery plan if compatibility is unknown.

Syntax references, checked 2026-09-16: [Claude marketplace management](https://code.claude.com/docs/en/discover-plugins), [Codex plugins](https://learn.chatgpt.com/docs/plugins), and local codex-cli 0.154.0 `plugin remove`, `plugin marketplace remove`, `plugin marketplace add` and `plugin marketplace --help` output. See the [M6 report](compatibility-m6.md) for actual testing evidence.
