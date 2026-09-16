# M6 release tooling and acceptance evidence

Date: 2026-09-16. Candidate version: **0.7.0**. This report distinguishes implemented release engineering from the public-release acceptance gate. No GitHub release has been published by this work.

## Delivered artifacts

`scripts/build_release.py` produces complete Claude Code and Codex local marketplace ZIPs, `release-info.json`, and `SHA256SUMS`. Archives contain only the explicit package allowlist, a host-specific installation guide and the MIT license. Runtime and outer ZIP entries use fixed timestamps, platform metadata and permissions. Repeated builds are reproducible with the same source and compression toolchain; different zlib/Python toolchains are not certified byte-identical.

The builder rejects unexpected package/output files and symlink/junction ancestry. Output paths must be canonical. Test temporary roots resolve legitimate operating-system aliases before testing deliberately introduced redirects. Release tests inspect nested runtime contents, verify checksums, run extracted launchers, check unchanged private state, reject tampering, and exercise Windows junction protections.

The existing standard-library zipapp remains the runtime. There are no additional dependencies, services, bundled paper readers or database migrations. The active per-plugin limit remains 262144 bytes, stricter than the design's 1 MiB first-release ceiling.

## Evidence matrix

| Environment/check | Result | Scope |
| --- | --- | --- |
| Windows, Python 3.13.5 | Automated regression suite executed | Runtime, service contracts, failure injection, packages and archive workflows |
| Claude Code 2.1.270 on Windows | Passed | Strict marketplace/plugin validation, isolated registration/install from extracted archive, installed-cache probe |
| codex-cli 0.154.0 on Windows | Passed | Isolated local marketplace registration/install from extracted archive, installed-cache probe |
| Both Windows hosts, 0.6.0 to 0.7.0 | Passed | Documented remove/re-register/install sequence in disposable host configurations; external synthetic configuration/SQLite unchanged |
| Existing personal Lark library | Passed, read-only | Installed candidate `doctor`: STATE_OK, AUTH_OK, BINDING_OK; healthy, no remote mutations or authorization reset |
| Fresh host/model natural-language workflow | Not executed | Installing a plugin and invoking its launcher does not prove host skill selection or scientific note quality |
| Real Lark create-new and read/publish/recovery | Not executed | Synthetic provider evidence is not service or tenant acceptance |
| macOS/Linux host installs and Python 3.11/3.12 | Not executed locally | CI matrix is configured, not yet observed running |
| Codex desktop fresh-session skill activation | Not executed | CLI installation evidence does not certify every Codex surface |

Host testing used private, isolated `CLAUDE_CONFIG_DIR` and `CODEX_HOME` directories. Existing user plugin registrations/caches were not replaced. Final archive installs were repeated after ZIP metadata fixes and installed runtime hashes matched the extracted artifacts. Tests included paths with spaces and Chinese characters and unrelated working directories. Probe did not create a private home.

Upgrade testing used the real prior 0.6.0 generated marketplace and the 0.7.0 candidate archive. SHA-256 comparisons confirmed the synthetic external state was unchanged. This establishes package replacement behavior, not every future schema downgrade. Setup recovery retains its exact runtime-version guard; reading artifacts have schema/hash checks but no exact runtime-release pin. The upgrade guide explains this difference and retains original packages for recovery.

The real-library doctor used the existing personal home/profile and existing Lark CLI 1.0.89 authorization. Configuration, binding and SQLite SHA-256 values were unchanged before/after diagnosis. No account identifiers, library URLs or diagnostic payloads are included in this report.

## Workflow and privacy verification

Archive tests run the real extracted launcher and its integrity check while substituting only a synthetic Lark process. They cover collection and duplicate prevention, pending status and English keyword reuse, fill-only preservation, filtered queries, priority updates, source ingestion, evidence-linked submission, publication and repeat/resume without duplicate notes, and Chinese library provisioning with completed-plan replay.

Synthetic fixtures remain in the source test suite, outside both archives. Release allowlists exclude local state, credentials, paper caches, development plans, tests and optional readers. Tests deliberately add private sentinel files and inspect both the outer archive and inner runtime to prove exclusion. Checksums detect corruption; they are not cryptographic publisher signatures.

Final full regression: **321 tests run, 320 passed, 1 skipped** in 197.850 seconds. The single skip is the existing Windows directory-symlink privilege check; new release junction-rejection tests executed successfully. Independent code review and scoped re-review found no remaining actionable issue after the two portability fixes. Public-file privacy scanning found no personal host/resource/path markers.

## Build measurements

| Artifact | Bytes |
| --- | ---: |
| Shared runtime | 88664 |
| Claude plugin | 105578 |
| Codex plugin | 106197 |
| Claude release ZIP | 100455 |
| Codex release ZIP | 100868 |

Runtime SHA-256: `b570902e16d123c257f022e1c1fbeeb185b05b00011b029aaed5c3c22dbffb7a`.

The exact archive hashes are recorded in generated `release-info.json` and `SHA256SUMS`; guide edits change ZIP hashes without changing runtime hashes.

Final integration also exposed a pre-existing LICENSE checkout line-ending difference between the main directory and the worktree. The release builder normalizes license text and `.gitattributes` now pins its LF checkout. A dedicated regression verifies byte-identical artifacts for LF/CRLF license inputs. This packaging-only fix does not change the runtime or installed plugin contents. After the fix, 21 release/package tests ran: 20 passed and the same existing symlink-privilege check skipped. Scoped review found no issue; main/worktree archive hashes now match.

## Release decision

M6 engineering deliverables are implemented. **The public-release certification gate remains open.** No platform/host combination is yet advertised as passing the complete model-driven and live-service end-to-end gate. Windows archive installation and runtime behavior have the narrower evidence listed above.

Before publishing a supported release, run the [release checklist](release-checklist.md), record model/tool traces and real outcomes in an explicitly selected test library, and record actual CI results and any newly advertised OS/host combinations. The configured GitHub Actions workflow uploads candidates only; it does not create tags or publish releases.
