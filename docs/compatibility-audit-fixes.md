# Paper2Lark 0.7.1 audit-fix compatibility report

This report records local implementation evidence for the nine defects found in the 0.7.0 code audit. Focused tests use temporary private homes and synthetic providers. Extracted Codex and Claude workflows keep the real package launcher and integrity check. Real Lark writes, model-driven host acceptance, plugin installation, authorization changes, publishing and public release certification are deferred.

## A1–A9 regression matrix

| ID | Corrected behavior | Focused test |
| --- | --- | --- |
| A1 | A collection fill rechecks every planned empty value and stops on a human title, status or keyword edit. | `tests.test_papers.CollectionTests.test_apply_rejects_human_edit_for_each_fill_field` |
| A2 | An unknown file-only create result is journaled and never recreated; explicit adoption verifies the frozen row. | `tests.test_papers.CollectionTests.test_uncertain_create_never_retries_after_metadata_edits`; `tests.test_commands.CommandTests.test_adopt_record_requires_apply_and_forwards_exact_record` |
| A3 | Run reservation commits only after durable initial artifacts; legacy initialization orphans require explicit local repair. | `tests.test_reservation_repair.ReservationRepairTests.test_process_termination_before_commit_rolls_back_and_replacement_succeeds`; `test_process_termination_after_commit_keeps_resumable_run` |
| A4 | Large verified notes retain compact operation receipts without persisted content and remain idempotent. | `tests.test_publishing.PublicationTests.test_large_verified_note_uses_compact_receipt_and_is_idempotent` |
| A5 | A matching stranded publication result completes locally without recomputing warnings or repeating remote writes. | `tests.test_publishing.PublicationTests.test_retry_adopts_stranded_result_without_recomputing_historical_warnings` |
| A6 | Human-only Markdown/XML sections protect nested descendants until an equal-or-higher heading boundary. | `tests.test_templates.TemplateTests.test_human_heading_protects_nested_descendants_until_matching_depth`; `test_xml_heading_depth_and_ambiguous_depth_are_conservative` |
| A7 | The bounded PDF tokenizer preserves supported literal/hex/TJ operand order and treats text/comments as data. | `tests.test_pdf_tokens.PdfTokenTests.test_tj_array_preserves_literal_hex_order_and_conservative_spacing`; `test_literal_text_is_not_reinterpreted_as_operators` |
| A8 | Iterative page traversal preserves Kids order and returns structured complexity errors for deep/bad graphs. | `tests.test_sources.SourceIngestionTests.test_iterative_page_tree_preserves_order_and_rejects_bad_graphs`; `test_deep_page_tree_returns_structured_complexity_error` |
| A9 | List, object, numeric, boolean and null source kinds return one `SOURCE_INVALID` response with no provider call or traceback. | `tests.test_identity.IdentityTests.test_source_kind_must_be_a_string_before_supported_kind_membership`; `tests.test_commands.CommandTests.test_unhashable_source_kind_is_one_structured_error_without_provider_call` |

## Recovery and compatibility evidence

`tests.test_release_workflows` executes uncertain collection/adoption, orphan repair, interrupted-completion adoption, and schema-v3 unfinished-run reading from both extracted 0.7.1 marketplaces with synthetic Lark services. The Task 3 multiprocessing regressions terminate real child processes after reservation insertion, during artifact creation, after manifest creation before commit, and after commit; they establish the durable boundary without another native fake executable.

The SQLite schema remains v3. The checked-in synthetic fixture was generated and revalidated by the exact original 0.7.0 runtime at commit `1d374f7c351532151031f5489d047a1ec08592ba`; its deterministic archive SHA-256 is `1d15b960eddf08a0d3f9a01364b74a209c27c502620324946511a2b4f0083cdb`. Each extracted 0.7.1 host copies that fixture into a temporary home, shows and resumes its `awaiting_source` run, and leaves the schema-v3 state, run artifacts and synthetic provider transcript byte-identical. Setup plans deliberately retain exact runtime matching: a 0.7.0 plan must be recovered with its original runtime, which should be retained during upgrade.

Release verification builds both hosts twice into a fresh directory, compares archive and runtime hashes, checks explicit nested allowlists, confirms zero third-party runtime dependencies, and enforces the 262144-byte budget per plugin. Exact final counts and hashes are recorded in the private implementation report.

## Limits

The local suite cannot prove behavior of a real Lark tenant, OAuth environment, Claude/Codex model session, or every PDF. The PDF implementation supports a bounded lexical subset; it does not provide OCR, font mapping or general PDF conformance. Lark offers no atomic compare-and-swap for the final remote read/write window, and cross-machine exactly-once behavior is not claimed.
