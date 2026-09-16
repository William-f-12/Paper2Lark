# Collect a Paper

Use this workflow when the user asks to collect or index a paper without reading it.

## Boundaries

Collect only. Do not fetch the paper, create a note, summarize it, or invoke document operations, `sci-extract`, `research-paper-review`, or `arxiv2agent` unless the user separately asks to read it. Do not run login, logout, bind, or authorization commands. Never print credentials or private configuration.

## Workflow

1. Resolve this `SKILL.md` to an absolute path. The plugin root is `../..` from its directory; resolve `scripts/paper2lark.py` there and invoke it with Python 3.11+. Put every global option, such as `--home` or `--profile`, before `papers`.

   For every command, require and parse exactly one JSON stdout document. Continue only when `ok` is true; otherwise branch on the stable error code.

2. Create private temporary JSON files outside the plugin package. Build a version-one query with `schema_version: 1` and `include_vocabulary: true`. Run:

   `python LAUNCHER [GLOBAL_OPTIONS] papers list --query QUERY`

3. Build a version-one add request from the explicit source and available metadata. Use an absolute path for a local file. Never guess a DOI, arXiv identity, or metadata. Match the live vocabulary semantically and reuse existing canonical English labels through `selected_existing`. Each keyword has at most three words; choose at most eight. Use `proposed_new` only when no existing label fits and the evidence supports the new label. Empty keywords are valid.

4. Run `papers add --input REQUEST` without `--apply`. Parse its one JSON result. Inspect `action`, field changes, canonical keywords, and vocabulary additions. Stop on an error or an unexpected preview.

5. Add `--apply` only when the user authorized collection. Inspect its parsed result. If its error code is `STATE_UNINITIALIZED`, run `python LAUNCHER [GLOBAL_OPTIONS] state init` once; retry that same apply once only after state init succeeds. Inspect the retry result. Do not retry uncertain remote writes or any other failure. For `COLLECTION_RESULT_UNCERTAIN`, stop: an `intended` journal without an ID can mean the process stopped either before or after dispatch. Inspect the named private operation artifact and the bound table. Use `papers add --input REQUEST --apply --adopt-record RECORD_ID` only after verifying the exact account, table, row identity and frozen intended fields. Never match by title. Version 0.7.0 has no collection journal; inspect existing rows before retrying a historical uncertain file-only add.

6. Remove the temporary files. Report `create`, `fill`, or `noop`; record identity only when returned; keyword additions; and `remote_mutations`. A successful repeat may be `noop` with no remote mutation.
