# Paper2Lark M4 Compatibility Report

**Date:** 2026-09-15<br>
**Version:** 0.5.0<br>
**Milestone:** verified Wiki-note publication, index reconciliation, and recovery

## Verified package

M4 was implemented and tested on Windows with Python 3.13.5. Claude and Codex packages contain the same dependency-free standard-library zipapp.

- Runtime SHA-256: `95623a18feacc6ecb46c9b97b1b700b0e88a3254fde5b8c7de2e9338ca6d6146`
- Runtime size: 76,375 bytes
- Claude package: 89,687 bytes
- Codex package: 90,239 bytes
- Third-party runtime dependencies: none
- Package ceiling: 262,144 bytes per host

Two consecutive builds produced identical package bytes. The builder's explicit source allowlist now includes the focused document and publishing modules.

## Automated verification

The final full suite ran 261 tests in 115.300 seconds: 260 passed and one Windows directory-symlink capability test was skipped. The review regressions were first observed failing, then passed after the fixes below.

Coverage includes:

- schema-v3 creation and validated v1/v2 migration with byte-for-byte backups, including completion of the early M4 v3 shape;
- durable one-active-persistent-run reservations per library paper;
- explicit idempotent local cancellation that retains artifacts, releases a reservation, and reports possible prior remote state;
- idempotent operation intents, monotonic outcomes, bounded snapshots, and historical verified baselines;
- an allowlisted shell-free `docs +create`, Markdown readback, Wiki child listing, and placement verification path;
- immutable plans bound to account, library, template, source, analysis, draft, record preconditions, child snapshot, and content digest;
- rejection of draft-only publication, changed templates, and incomplete full-reading coverage;
- note creation followed by exact UTF-8 content-digest, marker, document-revision, parent, and space verification;
- recovery from a lost create response by adopting exactly one newly observed matching document without a second create;
- recovery when a Base update response is lost, including both applied and unapplied remote outcomes;
- note-link and summary conflict blocking, plus preservation of user-changed status and keywords;
- a final managed-field precondition read immediately before Base batch update;
- adoption of a valid immutable plan left on disk by interruption before the run-manifest transition;
- successful no-op replay after completion;
- a complete synthetic subprocess workflow through the generated launcher: prepare, ingest, submit, plan, apply, show/resume, and repeated apply;
- identical read-skill bodies and launch behavior in generated Claude and Codex packages.

## Live versus synthetic coverage

M0 previously executed the underlying `docs +create --parent-token`, `docs +fetch`, `wiki +node-get`, Base update, and field-update primitives against disposable Lark resources. M2 and the 2026-09-15 audit verified the existing personal binding, account, fields, vocabulary, record reads, and current template read-only.

M4 remote writes to the personal production library were **not executed**. Publication and recovery were exercised through a synthetic Lark provider across the real native-executable subprocess boundary, including fault injection at both write boundaries. This proves runtime orchestration and local recovery behavior; it does not independently re-certify the current service's write semantics beyond the M0 primitive probes.

## Guarantees and limits

Paper2Lark records intent before each mutation and verifies remote state before advancing. A verified note precedes every index update. An uncertain document creation is never repeated automatically; recovery either adopts one exact candidate or retains `uncertain_remote_commit`. An identical completed plan is a no-op and reports no new remote mutations. A second persistent reading request for the same paper receives the existing active run ID.

M4 deliberately creates a separate note revision for rereads. It does not replace a collaborative document in place because M0 showed that the tested revision flag was not compare-and-swap. Same-machine locks, SQLite reservations and the operation journal provide cooperative recovery, not global exactly-once delivery across multiple machines or clients bypassing Paper2Lark. The Base batch-update endpoint exposes no atomic compare-and-swap precondition; Paper2Lark checks managed values in the last read before its write, but an external edit arriving after that read remains a service-level race.

The milestone does not provision or customize a new library; that remains M5. It also does not add scanned-PDF OCR, figure/table assets, arbitrary embedded-resource copying, or a general research knowledge agent. The existing small PDF and conservative template-protection limits still apply.
