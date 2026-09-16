# M5 provisioning and customization validation

Version 0.6.0 implements new Wiki/Base/template setup and additive existing-library migration. All development operations used isolated temporary homes and synthetic resources. No production Wiki/Base setup or migration writes were executed.

## Delivered behavior

- `setup plan` freezes the account, profile, baseline binding/schema, language and full proposed resources in private, immutable artifacts.
- `setup apply` journals intent before each write, captures IDs before verification, and binds only the exact verified physical resources.
- `setup show` inspects local progress. Explicit `setup cancel` retains every artifact and remote resource; it performs no remote operation.
- New libraries have an explicitly ordered 12-field index with Title primary, English seed vocabulary, language-specific status/priority labels, notes parent and research/review template. English and Chinese templates protect human-only sections.
- Migration preserves field IDs through renames, adds only missing logical fields, and accepts explicit compatible field/status remapping. It does not modify existing rows, columns, options, views or template contents.
- Known resources are verified and reused. An uncertain response is never blindly retried; exact supplied references must pass placement/type/schema/content checks before adoption. A profile reservation and physical-library lock coordinate same-machine work.
- Crash recovery recognizes a previously committed binding before rechecking resources that a user may have customized afterward.

## Verification coverage

Python 3.13.5 on Windows. Provider tests validate CLI allowlists, pagination, complete schema/options and exact template content. Core fault injection covers every fresh-create boundary, partially completed migration, wrong adoption, account switching, stale/missing binding, malformed journals, primary-field order and final resource identity substitution.

The real generated Claude and Codex launchers are exercised from temporary directories with a Python synthetic Lark process. Both English and Chinese create/verify/bind/replay flows pass. Other subprocess scenarios cover lost responses, verified adoption, local cancellation, malformed input and preservation of renamed fields and human-edited templates. No new native fake executable is built by these tests.

A live **read-only** migration plan also succeeded against the existing personal library, using a disposable copy of its configuration/binding. It resolved all 12 mapped fields, proposed zero additions and reported `remote_mutations: false`. SHA-256 comparisons confirmed the original configuration, binding and SQLite bytes remained unchanged. No new OAuth flow was started.

Build measurements (same runtime in both hosts):

| Artifact | Bytes |
| --- | ---: |
| Shared runtime | 88,664 |
| Claude plugin | 105,578 |
| Codex plugin | 106,197 |
| Enforced per-plugin budget | 262,144 |

Runtime SHA-256: `19ad681f11d82f0fc7686d64f6a8689d514090ba7b0482c0b1d7fd72621ff413`. Third-party dependencies: none. After normalizing source line endings to the repository LF policy, 12 package tests passed with the same single symlink skip. Package tests verify deterministic rebuilds and source allowlists. Full suite: **310 tests run, 309 passed, 1 skipped** in 151.789 seconds; the skip is the Windows directory-symlink permission test. Independent review found no remaining important issues after regression-tested fixes.

## Limits

Fresh-library production creation was **not executed**. CLI flags and field schemas were checked against local installed help and reference documentation; this establishes the adapter contract, not live tenant permission or service compatibility. Lark content normalization that changes the exact template digest causes a verification block and retains the created resource.

Recovery cannot infer whether a write reached Lark when no ID was recorded. A user/agent must inspect exact resources for adoption, or explicitly abandon the setup and inspect retained resources before replanning. There is no automatic deletion, destructive rollback or cross-machine exactly-once guarantee.

Existing setup artifacts require the recorded runtime version for apply. Reading state remains schema-v3; setup adds a separate atomic JSON journal and no database migration. Setup does not implicitly reset authorization, change language configuration or initialize SQLite. Optional paper readers remain external. M6 installation/release certification, macOS/Linux support evidence and real hosted-model end-to-end evaluation remain outstanding.
