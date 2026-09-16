# Read and Optionally Publish a Paper

Use this workflow for a review, local draft, or explicitly requested Paper2Lark library note.

## Boundaries

A draft-only request uses `persist_to_library: false`, makes no Base or Wiki mutation, and cannot be published. Read-and-save intent uses `persist_to_library: true`; use the add workflow first if no index record exists. The original request owns publication intent. Model artifacts cannot enable persistence.

## Workflow

1. Resolve this `SKILL.md` to an absolute path. The plugin root is `../..`; invoke `scripts/paper2lark.py` there with Python 3.11+. Put global options before commands and continue only when the single JSON stdout document has `ok: true`.

2. Write private input JSON outside the plugin. Include exactly one source or record, explicit persistence, `requested_depth`, reader preference and reread flag. For persistence, run `state init` before `read prepare --input REQUEST`. If prepare returns `ACTIVE_RUN_EXISTS`, resume the reported run instead of starting another. Open `handoff.json`; use returned identifiers, template blocks and paths.

3. Select at most one available optional reader. Honor an explicit choice. In auto mode, use `arxiv2agent` for arXiv acquisition, `sci-extract` for routine scientific reading, or `research-paper-review` for critical review. Do not install one. If unavailable, disclose the fallback and use host-native reading plus built-in ingestion.

4. Acquire the paper as bounded UTF-8 text or a supported text PDF. Run `sources ingest --run RUN --input SOURCE_INPUT`. Use actual coverage; an abstract stays abstract-only. Do not emit unsupported image-reference blocks.

5. Follow the handoff contracts. Outside the run directory, write `role-map.json` from observed selectors while preserving human blocks, evidence-linked `analysis.json`, and aligned `note-plan.json`. Keywords are English, at most three words each and eight total. Reuse live labels with `selected_existing`; use `proposed_new` only when none fits.

6. Run `read submit --run RUN --analysis ANALYSIS --note-plan NOTE_PLAN --roles ROLE_MAP`, then `runs show --run RUN`. Report the local draft, actual coverage, warnings, keywords and template revision. Submit alone is not publication.

7. Stop for draft-only intent. For read-and-save, run `publish plan --run RUN`. Inspect its title, digest and mutations. Planning rechecks the template, record, vocabulary and Wiki parent without remote writes.

8. Run `publish apply --run RUN --plan PLAN` with that immutable file. It verifies the complete content digest and Wiki placement before updating the index, preserves user-changed status/keywords, and blocks conflicting note-link/summary edits. Rereads create separate revisions.

9. After interruption or a non-completed result, run `runs resume --run RUN`. For `uncertain_remote_commit`, rerun the same apply command; do not repeat creation manually. Report `blocked_conflict` for resolution. If the user explicitly abandons a persistent run, `runs cancel --run RUN` retains artifacts and releases its reservation; remote state may remain after publishing began. Call it published only at `completed` with a verified note URL.
