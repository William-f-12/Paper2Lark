# Install Paper2Lark for Codex CLI

This guide describes the archive distribution format. If no release asset has been published yet, build a local candidate from the repository with `python scripts/build_release.py`; the ZIPs are written to `.paper2lark-work/releases/`. A local build is not a certified public release.

Download the Codex ZIP from the [release assets](https://github.com/William-f-12/Paper2Lark/releases), compare its SHA-256 with the release checksum file, and extract it into a dedicated, versioned directory. The extracted root must contain `.agents/plugins/marketplace.json` and `plugins/paper2lark/`. Register that complete root, not the plugin subdirectory. The source repository root is not a built marketplace.

From the extracted root in a terminal:

```powershell
codex plugin marketplace add .
codex plugin add paper2lark@paper2lark-local
```

Start a new Codex session and ask: "Use paper2lark-probe to verify the installed runtime." This local probe needs no Lark authorization. The plugin also provides `paper2lark-doctor`, `paper2lark-setup`, `paper2lark-add`, `paper2lark-library`, and `paper2lark-read`. For example: "Use paper2lark-doctor for an offline diagnosis; use my existing home and personal profile."

These commands modify the current Codex installation's plugin configuration. If you already registered `paper2lark-local`, follow the upgrade guide before replacing it. Do not create a second marketplace identity for the same plugin.

## Prerequisites and private state

Install Python 3.11+ and configure `lark-cli` separately for your own Lark account. The runtime uses Python's standard library; no pip dependencies or optional paper-reading skills are required. Host access to Python, the CLI, private state and the network remains subject to host permissions.

Choose an absolute private directory outside the extracted package, repository and host plugin cache. The default is `~/.paper2lark`. Both hosts share state only when they use the same home and profile. In PowerShell, before launching either host:

```powershell
$env:PAPER2LARK_HOME = "C:/Users/YOU/.paper2lark"
$env:PAPER2LARK_PROFILE = "personal"
```

Replace `YOU` with your account. Existing installations should retain their current home and profile. Environment variables set in a terminal apply to processes launched from that terminal; a separately started desktop app may not inherit them. Explicit `--home` and `--profile` before every runtime command remove this ambiguity.

From the extracted marketplace root, initialize a new home explicitly:

```powershell
python ./plugins/paper2lark/scripts/paper2lark.py --home "C:/Users/YOU/.paper2lark" --profile personal config init
python ./plugins/paper2lark/scripts/paper2lark.py --home "C:/Users/YOU/.paper2lark" --profile personal state init
python ./plugins/paper2lark/scripts/paper2lark.py --home "C:/Users/YOU/.paper2lark" --profile personal doctor --offline
```

`config init` preserves an existing configuration. `state init` explicitly initializes or migrates validated state, backing up migrations. Installation does not create a Lark library or reset authentication. Use the setup skill to create a library or bind an existing one, reviewing the selected account, resources and any proposed remote changes.

If credentials appear unavailable only inside the host sandbox, compare the same read-only diagnosis in your normal terminal. Do not log out or reset authorization to diagnose a sandbox restriction.

## Verification and further use

A successful local probe proves package loading and runtime execution only. It does not certify model-driven reading, publication or live Lark provisioning. Consult the [M6 compatibility report](https://github.com/William-f-12/Paper2Lark/blob/main/docs/compatibility-m6.md) for the exact tested host/OS combinations and outstanding checks. macOS/Linux and desktop installation are not supported release combinations without recorded archive installation and end-to-end evidence.

See the [workflow guide](https://github.com/William-f-12/Paper2Lark/blob/main/README.md) and [upgrade/recovery guide](https://github.com/William-f-12/Paper2Lark/blob/main/docs/upgrading.md). Keep the extracted marketplace and original release archive for upgrades and recovery.

The exact shell syntax was verified using `codex plugin --help`, `codex plugin marketplace add --help` and `codex plugin add --help` with codex-cli 0.154.0 on 2026-09-16. The [official plugin guide](https://learn.chatgpt.com/docs/plugins) documents `/plugins` and starting a new session after installation. It does not establish that every desktop or IDE surface supports this local archive workflow.
