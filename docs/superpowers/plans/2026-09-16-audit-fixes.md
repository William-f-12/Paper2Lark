# Audit fixes implementation plan

> **For agentic workers:** Use superpowers:executing-plans or superpowers:subagent-driven-development to implement and review tasks individually after implementation is authorized.

**Goal:** Fix the nine confirmed defects found in the 0.7.0 audit without increasing runtime dependencies or changing the user's live library.

**Architecture:** Reuse existing Base preconditions, SQLite transactions, advisory locks and atomic private artifacts. Add a narrowly scoped collection-intent journal, compact publication receipts, recoverable completion, hierarchical template protection and a bounded tokenizer for the existing PDF subset.

**Tech Stack:** Python 3.11+ standard library; existing lark-cli; unittest; existing two-host zipapp build.

**Spec:** `docs/superpowers/specs/2026-09-13-paper2lark-design.md`. This plan supplies the defect-specific requirements below and does not extend the advertised PDF or host support surface.

**Status:** Proposed design only. No runtime changes or repair operations performed. Target patch release: 0.7.1, after verification.

## Global constraints

- Zero new third-party runtime dependencies. Keep each generated plugin at or below 262144 bytes.
- English source/docs/skills; preserve configured Chinese note/library behavior and English keywords, max three words and eight labels.
- No real Lark mutations, authentication reset, personal configuration edits or installation changes during implementation tests.
- Preserve existing SQLite schema v3 where possible. The planned collection journal is a separate versioned JSON artifact, not an implicit database migration.
- Do not delete old journals, run artifacts or remote resources to clear an error.
- Preserve `doctor` and `runs resume` as read-only commands. Any orphan repair is explicit and previewable.
- Tests must reproduce the old failure before implementing each fix. Use standard-library Python child processes for abrupt-exit tests; do not introduce another native fake-lark executable.
- Upgrade both host sessions together and stop older runtime writers before using the new journal. Concurrent 0.7.0 and patched writers against one home are not supported; the older runtime cannot honor the new journal.
- Do not claim cross-machine exactly-once behavior or atomic compare-and-swap from Lark. Preserve the documented final remote read/write race limitation.

## Defect inventory and batches

| ID | Severity | Defect | Task |
| --- | --- | --- | --- |
| A1 | P1 | Fill-only write ignores a human edit observed before writing | 1 |
| A2 | P2 | Uncertain file-only creation duplicates on retry | 2 |
| A3 | P2 | Reservation survives without a recoverable run | 3 |
| A4 | P2 | Full document exceeds publication journal limit | 4 |
| A5 | P2 | Stranded completion artifact cannot be adopted | 5 |
| A6 | P2 | Human-only protection lost at nested headings | 6 |
| A7 | P2 | PDF text tokens lost/reordered | 7 |
| A8 | P2 | Deep PDF tree escapes bounded error handling | 7 |
| A9 | P3 | Non-string source kind raises raw TypeError | 8 |

Batch A: tasks 1-2, protecting existing remote data and preventing repeated creation.
Batch B: tasks 3-5, making initialization and publication interruptions recoverable.
Batch C: tasks 6-8, preserving template ownership and source fidelity, then task 9 release checks.

## Task 1: Use existing fill preconditions (A1)

**Files:** `src/paper2lark/papers.py`; `tests/test_papers.py`, `tests/test_base.py`.

**Interface:** Keep `LarkBase.update_record(record_id, fields, expected_fields=None)` unchanged. Collection supplies the prior logical value for every field it intends to fill. Explicit user-requested updates retain their current semantics.

- [ ] Reproduce with the production gateway: planning sees an empty title; its pre-write read sees `Human title`; no write may follow.
- [ ] Confirm the regression fails against 0.7.0.
- [ ] Pass deep-copied expected values from the final collection plan:

```python
expected = {key: copy.deepcopy(plan['target']['fields'].get(key))
            for key in plan['changes']}
verified = gateway.update_record(record_id, copy.deepcopy(plan['changes']),
                                 expected_fields=expected)
```

- [ ] Assert `INDEX_CONFLICT`, zero update calls and retained human text. Test title, status and keywords; also prove unchanged empty values still fill successfully.
- [ ] Run `python -m unittest tests.test_papers tests.test_base -v`, review and commit the isolated fix.

This closes the omitted gateway guard; it cannot eliminate edits occurring after the final Lark read and before the write.

## Task 2: Durable collection intent and explicit reconciliation (A2)

**Files:** create `src/paper2lark/collection_journal.py`, `tests/test_collection_journal.py`; modify `papers.py`, `base.py`, `__main__.py`, `skill_sources/add.md`, build allowlists and their tests.

**Storage:** `<home>/collections/<library_id>/<operation_uuid>.json`. Version 1; bounded atomic writes, private permissions, safe canonical paths. Store account/binding digest, canonical aliases/fingerprint, frozen intended fields and request digest, operation state, and optional record ID. No paper binary or credentials. Use the existing physical-library lock for all journal lookup/update and collection writes.

**Interfaces to add:** journal service `begin_intent(home, binding, identity, fields) -> dict`, `find_pending(home, library_id, aliases) -> dict | None`, `record_created(home, intent, record_id) -> dict`, `finish_intent(home, intent) -> dict`. `begin_intent` saves intent before dispatching the record-create call. Matching a pending operation uses intersecting aliases, not the entire mutable request digest; edited metadata must not create a second pending intent.

**States:** `intended -> created -> verified -> completed`. `intended` without an ID is uncertain, including a crash just before dispatch. Do not infer that no write occurred. `created` is saved as soon as the response exposes an ID, before fallible readback; split the existing create-and-verify adapter path accordingly while retaining its public convenience method.

**CLI extension:** `papers add --input REQUEST --apply --adopt-record RECORD_ID`. Reject adoption without `--apply` or without a matching uncertain operation. Adoption verifies the bound account/table, supplied record identity and frozen intended fields. It is explicit reconciliation, never an automatic title-only match. A mismatch blocks with details sufficient for user resolution and performs no record creation/update. An unknown-ID pending operation returns `COLLECTION_RESULT_UNCERTAIN` and points to the private operation artifact.

- [ ] Reproduce commit-then-timeout for file-only input. Assert that the same request, including one with edited metadata, never dispatches a second create.
- [ ] Cover process exit after intent persistence, after receiving an ID, after readback and after local identity synchronization.
- [ ] On known-ID retry, read/verify that record and finish local synchronization. Never recreate it if missing or mismatched.
- [ ] Complete the journal only after local paper identity has been synchronized; a retry between sync and completion repeats safe local reconciliation.
- [ ] Cover accepted explicit adoption, wrong record/table/account, corrupted journal, simultaneous callers and alias intersection.
- [ ] Verify preview remains read-only, DOI/arXiv/URL collection and ordinary successful file deduplication still work, and no new remote field is required.
- [ ] Add the module to both explicit runtime allowlists; run collection, adapter, command and package tests, review and commit.

Legacy 0.7.0 uncertain creations have no journal. Do not promise automatic recovery of those historical writes; document explicit inspection of existing rows before retrying file-only collection. Do not silently backfill remote fingerprints or merge/delete duplicates.

## Task 3: Transactional run reservation and legacy orphan repair (A3)

**Files:** `state.py`, `reading.py`, `runs.py`, `publishing.py`, `__main__.py`, `doctor.py`; `tests/test_publication_state.py`, `tests/test_read_commands.py`, `tests/test_runs.py`.

**Interface:** Add `state.reserving_run(home, library_id, paper_uid, run_id)` as a context manager. Reuse the existing ownership/uniqueness checks, begin one SQLite transaction, insert the reservation, yield for local run initialization, then commit only after all initial artifacts/manifest are durable. Roll back on any nonlocal exit, including BaseException; closing a killed process's connection must leave no committed reservation. Retain the old `reserve_run` API for existing callers/tests through the same implementation.

```python
with library_lock(home, binding['library_id']):
    with state.reserving_run(home, binding['library_id'], paper_uid, run_id):
        run = create_run(home, request, template, handoff,
                         paper_uid=paper_uid, run_id=run_id)
        verify_run_artifacts(Path(run['run_dir']), run)
```

All network reads happen before this transaction. The transaction must contain no model wait or Lark call. An interruption before commit may retain unreserved local artifacts; retain them, do not let them publish without the existing active-run ownership check, and do not block future prepare attempts. This is recoverable ordering, not a claim of one transaction spanning SQLite and the filesystem.

**Legacy repair command:** `runs repair-reservation --run RUN_ID` previews; adding `--apply` performs a local-only repair. Resolve the active entry through SQLite, validate the selected library/profile and exact owner, acquire the same physical-library lock as prepare, and require no publication operations for that run. Release only an initialization orphan with absent/incomplete initial run state. A valid run directs the caller to normal resume/cancel; a corrupted established run or evidence of remote publication blocks repair. Retain all partial files. Never release by age alone. `doctor` can report the orphan but cannot repair it.

- [ ] Kill a Python child after insertion, during artifact creation and after manifest creation but before commit. Assert no stranded committed reservation; subsequent preparation succeeds.
- [ ] Kill after commit and assert the manifest/artifacts exist and normal resume is available.
- [ ] Verify two concurrent prepares yield one active reservation, without deleting the other caller's artifacts.
- [ ] Reproduce a legacy reservation with no run directory; preview changes nothing, apply releases only that reservation, repeat apply is a no-op.
- [ ] Reject repairs of valid active runs, different libraries, publication-operation evidence and unsafe paths.
- [ ] Run state/run/read/publication tests, review transaction duration and rollback behavior, then commit.

## Task 4: Compact verified publication receipts (A4)

**Files:** `publishing.py`, `documents.py` if necessary; `tests/test_publishing.py`, `tests/test_publication_state.py`.

**Interface:** Keep full content for in-memory digest verification. Persist a whitelist receipt containing document ID, node token, integer revision, verified content digest and verified note URL (optional bounded title if consumed). Do not persist `content` or arbitrary provider fields in an operation response. Apply this to fresh creation, known-ID retries and recovered unknown-response creations.

- [ ] Reproduce a valid 270,000-byte note failing after creation; retain a supported near-limit case and a Chinese-text case.
- [ ] Change the verified-response projection, keeping the existing 256 KiB journal bound and 4 MiB artifact bound.
- [ ] Assert completed publication, one creation, one index update, compact operation JSON without content, and idempotent retry.
- [ ] Verify a legacy `applied` operation can resume through the compact projection. Legacy verified receipts containing content remain readable; no bulk rewrite is needed.
- [ ] Oversize input still fails before a remote creation. Test real adapter readback normalization and strict digest checks remain unchanged.
- [ ] Run publication/state/document tests, review and commit.

## Task 5: Adopt stranded completion artifacts safely (A5)

**Files:** `publishing.py`, `runs.py` only if a bounded existing-artifact descriptor helper is needed; `tests/test_publishing.py`.

**Interface:** Before recomputing final warnings or reapplying operations, detect an existing unmanifested `publication-result.json`. Read it with the existing bounded strict JSON/path rules. Adopt only when it matches the current run, immutable plan, both verified operation identities and saved baseline. Confirm expected IDs/URL/completed result shape, correct binding and active owner. Do not trust merely the presence of the file, and do not overwrite it.

- [ ] Interrupt between result-file write and completed-manifest transition; change live status/keywords; assert retry adopts the saved result, completes and releases the reservation with no additional remote mutation.
- [ ] Freeze warnings as the historical result of the committed operation. If current observations are returned, keep them separate from the immutable saved result.
- [ ] Tampered JSON, wrong run/record/document, unexpected URL, absent/unverified operations, wrong baseline or malformed result must block adoption.
- [ ] Cover interruption after completed manifest but before reservation release; repeat apply releases the exact reservation safely without touching a newer run.
- [ ] Keep normal precommit conflict detection intact. A valid completed result is a commit record, not a demand to reset later human edits.
- [ ] Run publication/run/state tests, review and commit.

## Task 6: Hierarchical human-only template protection (A6)

**Files:** `templates.py`; `tests/test_templates.py`, `tests/test_read_submission.py`.

**Design:** Preserve heading depth internally for Markdown and recognized XML heading forms. Protect a human-only section and all descendants until a heading of equal or smaller depth ends it. Retain the existing behavior where a manual-only instruction in the section body protects that section. For ambiguous XML heading depth, protect conservatively rather than guessing that a human section ended.

Keep the exported snapshot/role-map schema and existing selector construction stable where possible; heading levels can stay internal to parsing. Do not invalidate existing in-progress template snapshots or silently reinterpret their saved role maps. Apply corrected protection to new snapshots; document that old drafts retain their frozen snapshot.

- [ ] Reproduce `# Personal Notes (human only) / ## Questions / body`; assert parent, child and body protected, and a later top-level Summary writable.
- [ ] Cover three levels, sibling children, body-only manual markers, Chinese markers, skipped heading depths, Markdown and XML.
- [ ] Assert role-map validation rejects AI/mixed ownership of protected descendants; legitimate AI siblings still submit.
- [ ] Run template/read-submission/reading-contract tests, review and commit.

## Task 7: Bounded PDF lexical parsing and page traversal (A7, A8)

**Files:** create `src/paper2lark/pdf_tokens.py` if tokenizer separation keeps `sources.py` focused; modify `sources.py`, build allowlists and `tests/test_sources.py`; add `tests/test_pdf_tokens.py` if the module is created.

**Design:** Replace regex BT/ET splitting with a small tokenizer for the already supported subset. Recognize comments, operators, literal strings (escaped characters and balanced parentheses), hex strings, arrays and numeric operands. Only operator tokens change BT/ET state; text inside strings never does. Emit text from supported Tj/TJ/quote operators in operand order. Respect TJ spacing adjustments conservatively and document text-layout limits; do not claim font mapping, OCR or general PDF conformance. Malformed/unsupported constructs return a structured error rather than guessed text.

**Proposed interface:** `extract_text(data: bytes) -> str` in `pdf_tokens.py`, called by `_content_text`. Bound token count to 1,000,000 and lexical nesting to 64; existing stream decompression/size limits remain. Exceeding either bound returns `SOURCE_TOO_COMPLEX`.

Replace recursive Pages traversal with iterative depth-first traversal preserving Kids order. Keep visited/active-node tracking for cycles/duplicates. Bound nesting to 256 and visited nodes to 100,000, in addition to existing page/section limits. These explicit safety caps may reject unusual PDFs; report this as a limitation instead of silently truncating.

- [ ] Regression: `BT (METHOD results 42) Tj ET` preserves all text.
- [ ] Regression: `BT [(Accuracy ) <3935> ( percent)] TJ ET` produces `Accuracy 95 percent` in that order.
- [ ] Cover escaped/nested parentheses, ET/BT inside strings/comments, alternating literal/hex operands, consecutive text objects and malformed delimiters.
- [ ] Cover a 1,100-level Pages chain, cycles, duplicate nodes, valid nested trees and page-order preservation. Assert structured errors, never raw RecursionError.
- [ ] Exercise ingestion through the actual command wrapper, not only private parser helpers; retain existing supported PDF fixtures.
- [ ] Run source/contract/command/package tests and inspect token/byte limits. Review and commit without adding a dependency.

## Task 8: Validate source kind before membership (A9)

**Files:** `identity.py`; `tests/test_identity.py`, `tests/test_commands.py`.

```python
if (not isinstance(kind, str)
        or kind not in {'auto', 'doi', 'arxiv', 'url', 'file', 'text'}
        or not isinstance(value, str)):
    _error('SOURCE_INVALID', 'source requires a supported kind and string value')
```

- [ ] Add failing cases for kind as list, object, number, boolean and null.
- [ ] Assert domain error `SOURCE_INVALID`; through the launcher assert exit 2, one JSON stdout document, no traceback and no provider call.
- [ ] Apply the type guard. Do not add a catch-all Exception handler that hides programming errors.
- [ ] Run identity/command tests, review and commit.

## Task 9: Integration, compatibility and release evidence

**Files:** affected tests/build allowlists, `README.md`, `docs/upgrading.md`, `skill_sources/add.md`, `skill_sources/read.md`, new `docs/compatibility-audit-fixes.md`, version in `src/paper2lark/__init__.py` and version assertions.

- [ ] Verify the nine audit reproducers against the original revision fail for the expected reasons and pass after fixes.
- [ ] Extend extracted-package workflow tests for uncertain collection/adoption, orphan repair and interrupted completion across both host launchers.
- [ ] Add genuine child-process termination tests for durable-write boundaries; use temporary private homes and synthetic Lark services only.
- [ ] Run `python -m unittest discover -s tests -v` after all fixes. Record exact pass/skip counts; do not rely on the prior 322-test baseline.
- [ ] Build both 0.7.1 artifacts into a new output directory, compare shared runtime hashes, deterministic repeat builds, nested allowlists and the 262144-byte per-plugin budget. Include each new module explicitly.
- [ ] Confirm old 0.7.0 schema-v3 homes and valid unfinished runs remain readable, and setup plans retain their existing exact-version behavior. Document original-runtime retention for old setup plans.
- [ ] Review public fixtures/docs for private data. Update English command/skill instructions for uncertain collection and explicit orphan repair.
- [ ] Obtain independent review of crash boundaries and data-preservation paths; resolve actionable findings before integration.
- [ ] Report implementation/test evidence separately from real Lark/host acceptance, which the user has deferred. Do not publish, replace the installed plugin or mutate the personal library as part of these fixes.

## Plan self-review

All A1-A9 have a task and a negative/positive acceptance scenario. Tasks 2, 3 and 9 share CLI/skills/build files and must integrate sequentially. Tasks 4 and 5 share publication state handling and run in that order. Tasks 6 and 7 can be implemented independently after their interfaces are frozen. The journal's new module and optional PDF tokenizer must be added to both explicit allowlists; tests cannot depend on unshipped source files. Pending-task and artifact compatibility checks apply before the patch-version bump is finalized.
