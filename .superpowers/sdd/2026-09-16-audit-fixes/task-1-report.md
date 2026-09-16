# Task 1 — Existing fill preconditions (A1)

## Scope

Implemented collection-only fill preconditions on branch fix/audit-defects-0.7.1. LarkBase.update_record(record_id, fields, expected_fields=None) and explicit update_paper semantics were left unchanged.

## TDD record

1. Added a regression test for an existing record whose planned title was empty, then simulated a human title edit during the gateway pre-write phase.
2. Ran the regression against the pre-fix collection code:

   python -m unittest tests.test_papers.CollectionTests.test_apply_rejects_human_edit_between_plan_and_fill -v

   Result: FAIL; Paper2LarkError was not raised, demonstrating that collection proceeded to write after the human edit.
3. Added the minimal collection change: build expected from deep-copied plan["target"]["fields"] for every key in plan["changes"], and pass it to gateway.update_record(..., expected_fields=expected).
4. Added coverage for title, reading_status, and keywords conflicts. Each asserts INDEX_CONFLICT, retained human text/value, and zero actual update writes. Added success coverage proving unchanged empty values still fill.
5. Focused green run:

   python -m unittest tests.test_papers.CollectionTests.test_apply_rejects_human_edit_for_each_fill_field tests.test_papers.CollectionTests.test_apply_fills_empty_values_when_preconditions_remain_unchanged -v

   Result: 2 tests passed.
6. Required full run:

   python -m unittest tests.test_papers tests.test_base -v

   Result: 60 tests passed.

## Implementation

Collection update applies the final plan's prior logical field values as gateway preconditions, with deep copies for both expected values and changes. The test gateway now models expected-field conflicts and distinguishes attempted gateway calls from actual writes.

## Commit

Commit hash: recorded by the enclosing git commit.

## Concerns

The unavoidable race after the gateway's final pre-write read remains an explicit project limitation. This change does not claim to eliminate that window.