# Manage a Paper Library

Use this workflow to list or search a bound paper index, or to change reading status, priority, or keywords when the user explicitly requests it.

## Boundaries

List/search is read-only and never changes reading status. Do not infer that seeing a paper means it was read. Do not run login, logout, bind, paper readers, document operations, or publish notes. Never reveal private configuration.

## Workflow

1. Resolve this `SKILL.md` to an absolute path. The plugin root is `../..` from its directory; resolve `scripts/paper2lark.py` there and invoke it with Python 3.11+. Put every global option before `papers`. For every command, require and parse exactly one JSON stdout document.

2. Create bounded version-one JSON in private temporary files outside the plugin. For `papers list --query QUERY`, set `schema_version: 1`, a bounded `limit`, and optionally `include_vocabulary`. Use only supported intersection filters: `record_ids`, configured `statuses`, exact `priorities`, exact canonical `keywords`, `year_from`, `year_to`, and case-insensitive `text`. Remove temporary files after use.

3. Before a change, query and uniquely identify the record. Build a version-one request for `papers update --input REQUEST` containing only the explicitly requested changes. Supported changes are reading status, priority, and keywords.

4. A keyword change replaces the complete selected set. Query the vocabulary, reuse canonical English labels through `selected_existing`, keep each label at most three words and the set at most eight, and use `proposed_new` only with evidence that no existing label fits. Show the full replacement; do not silently retain or remove labels.

5. Run update without `--apply`. Parse the one JSON result and show the exact previewed field changes. Add `--apply` only when the request authorizes those changes. If apply returns `STATE_UNINITIALIZED`, run `state init` once; retry that same apply once only after initialization succeeds. Do not retry an uncertain remote result or another failure.

6. Report `update` or `noop`, the changed fields, and `remote_mutations`. Stop on non-unique results, unexpected previews, unsupported filters, or stable errors.
