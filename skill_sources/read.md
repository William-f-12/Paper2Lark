# Read and Optionally Publish a Paper

Use for a review, local draft, or explicitly requested Paper2Lark library note.

## Boundaries

`persist_to_library: false` makes no Base/Wiki mutation and cannot be published. Read-and-save uses `true` and the add workflow when no index record exists. The original request alone authorizes persistence.

## Workflow

1. Resolve this `SKILL.md`. The plugin root is `../..`; invoke `scripts/paper2lark.py` with Python 3.11+. Put global options before commands. Continue only for one JSON stdout document with `ok: true`.

2. Write private input JSON outside the plugin with one source or record, persistence, `requested_depth`, reader preference and reread flag. For persistence, run `state init` before `read prepare --input REQUEST`. On `ACTIVE_RUN_EXISTS`, resume that run. Use the returned `handoff.json` identifiers, template blocks and paths.

3. Use at most one installed optional reader and honor explicit choice: `arxiv2agent` for arXiv acquisition, `sci-extract` for scientific reading, or `research-paper-review` for critique. Do not install one. Otherwise disclose that fallback: host-native reading plus built-in ingestion.

4. Acquire bounded UTF-8 text or a supported text PDF. Run `sources ingest --run RUN --input SOURCE_INPUT`. Preserve actual coverage; an abstract stays abstract-only. Do not emit unsupported image references.

5. Follow the handoff contracts. Outside the run directory, write `role-map.json` from observed selectors while preserving human blocks, evidence-linked `analysis.json`, and aligned `note-plan.json`. Keywords are English, at most three words and eight labels. Reuse `selected_existing` before `proposed_new`.

6. Run `read submit --run RUN --analysis ANALYSIS --note-plan NOTE_PLAN --roles ROLE_MAP`, then `runs show --run RUN`. Report the local draft, coverage, warnings, keywords and template revision. Submit is not publication.

7. Stop for draft-only work. For read-and-save, run `publish plan --run RUN` and inspect its title, digest and mutations. Planning rechecks remote inputs without writing.

8. Apply that exact file with `publish apply --run RUN --plan PLAN`. It verifies content and Wiki placement, preserves user-changed status/keywords, and blocks conflicting note-link/summary edits. Rereads create separate revisions.

9. After interruption, run `runs resume --run RUN`. For `uncertain_remote_commit`, rerun the same apply; do not repeat creation manually. Verified operations store compact receipts. A stranded completion is adopted only after operation, baseline and owner validation, without another remote write. Report `blocked_conflict`. `runs cancel` explicitly retains artifacts and releases a reservation; remote state may remain. Claim publication only at `completed` with a verified URL.

10. For a diagnosed legacy initialization orphan, preview `runs repair-reservation --run RUN`. Use `--apply` only after review. It retains partial files and contacts no Lark service. Never repair a valid run, publication evidence, unknown artifacts, a different owner, or based on age.
