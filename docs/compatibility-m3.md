# Paper2Lark M3 Compatibility Report

For the subsequent functional audit, real-template parser fixes, and updated development-build hash, see [the 2026-09-15 audit](audit-2026-09-15.md).

**Date:** 2026-09-15<br>
**Version:** 0.4.0<br>
**Milestone:** Source, model, and current-template handoff with verified local drafts

## Verified environment and package

M3 was implemented and tested on Windows with Python 3.13.5. The generated Claude and Codex packages contain the same deterministic, standard library zipapp and no third-party runtime dependency.

- Runtime SHA-256: `5f5c2c6c7f8c2b981cc7e584167adddfa35bbceebd8fc42ce39afce1c4372f94`
- Runtime size: 62,912 bytes
- Claude plugin: 75,737 bytes
- Codex plugin: 76,292 bytes
- Active package ceiling: 262,144 bytes per host

The package builder uses an explicit source allowlist, rejects unknown or redirected output files, records fixed-point byte counts, and reproduces identical files on a second build.

## Verified M3 behavior

The full suite contains 227 tests. The expected Windows directory-symlink capability check is the only skipped test. The final acceptance covers:

- versioned read requests selecting exactly one Base record or direct source;
- self-contained handoffs with concrete run/paper IDs, artifact paths, observed template blocks and host-written schemas;
- write-once private run artifacts, full manifest hash verification and fail-closed stage transitions;
- retry-safe, hash-bound submission timestamps stored in an atomic manifested intermediate state;
- bounded UTF-8 full-text and abstract ingestion;
- dependency-free extraction of ordinary and Flate-compressed text PDFs, with `/Pages` tree ordering and explicit unverified-order fallback;
- verified page numbering that retains blank page positions;
- section/page limits enforced before any source artifact is written;
- explicit `complete`, `partial`, `unavailable`, and `not_applicable` component coverage;
- abstract-only sources retaining unavailable main-text coverage and an `ABSTRACT_ONLY` warning;
- current Lark template snapshots with stable selectors, ordered roles and complete-section human protection;
- distinct stable UUIDs for URL-only sources and reuse of tracked record UUIDs;
- research and review analysis contracts, evidence locators, coverage limits, and live English keyword validation;
- deterministic Markdown rendering with source, coverage, template and index provenance;
- identical optional-skill-free prepare/ingest/submit behavior through the generated Claude and Codex launchers;
- zero remote writes throughout draft-only M3 acceptance.

The synthetic Lark provider is invoked through the real subprocess boundary. It verifies user-account checks, complete keyword-field reads, exact-record reads, current-template fetches and zero Base/Doc/Wiki mutation commands. M2 collection/update tests remain in the same regression suite.

## Capability boundaries

M3 can create a local template-aware draft. It does not publish or update a Lark Wiki note, set the index note URL or generated summary, change reading status, or provision a library. Those operations remain M4 and M5 work.

The built-in PDF parser is deliberately small. It supports bounded, unencrypted text PDFs using classic page/content objects, literal or hexadecimal text operands, and common Flate streams. It does not claim complete handling of scanned pages, OCR, encrypted files, arbitrary object streams, custom font maps, figures, tables, or supplementary files. M3 does not ingest visual assets or accept image-reference note blocks; equations are text-only note blocks. Unsupported extraction produces an actionable error or partial coverage.

Live M3 drafting against the personal Lark template was not tested in this report. Existing-library credentials, field mappings, vocabulary and template access were previously verified read-only during M2 acceptance. Wiki publication was not tested because M3 contains no publication command.

Optional `sci-extract`, `research-paper-review`, and `arxiv2agent` integrations were not redistributed or required for acceptance. The read skill selects at most one available integration and otherwise uses host-native reading plus the same runtime contracts.
