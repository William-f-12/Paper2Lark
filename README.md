# Paper2Lark

Latest verification: [functional audit and fixes, 2026-09-15](docs/audit-2026-09-15.md).

Paper2Lark is a lightweight local Claude Code and Codex plugin for managing and reading papers with an existing Lark library.

**Current milestone: M4 — publication and recovery (0.5.0).** It can collect/query/update an existing index, create evidence-linked local drafts, publish verified Wiki notes, reconcile their Base records, and resume partial or uncertain writes. M4 does not provision a new library; setup and customization remain M5.

## Build and test

Requires Python 3.11+. Windows with Python 3.13.5 is the tested environment. The runtime uses only the standard library; builds download no dependencies.

```powershell
python -m unittest discover -s tests -v
python scripts/build_plugins.py
```

Generated plugin roots are `dist/claude/plugins/paper2lark` and `dist/codex/plugins/paper2lark`. Both contain the same hash-pinned runtime. The builder refuses unknown files and redirected paths under `dist`; keep personal files elsewhere. Do not distribute an interrupted or failed build.

## Host installation

For a Claude session:

```powershell
claude --plugin-dir ./dist/claude/plugins/paper2lark
```

Invoke `/paper2lark:probe` for a local runtime check, `/paper2lark:doctor` for diagnosis, `/paper2lark:add` to collect without reading, `/paper2lark:library` to query/update the index, or `/paper2lark:read` to create and optionally publish a reading note.

For Codex:

```powershell
codex plugin marketplace add ./dist/codex
codex plugin add paper2lark@paper2lark-local
```

Start a new session and ask it to use `paper2lark-probe`, `paper2lark-doctor`, `paper2lark-add`, `paper2lark-library` or `paper2lark-read`. These commands register a local marketplace and change the selected host's plugin configuration. A previously installed development version may need reinstalling when the package version changes. Desktop UI installation is not separately certified.

## Private configuration

The default state root is `~/.paper2lark`, shared by both hosts and independent of plugin caches. Set `PAPER2LARK_HOME` to an absolute path to use a different root. No `.env` is loaded automatically.

The following commands use the Codex package's launcher; the Claude launcher's commands are identical. Global options (`--home`, `--profile`, `--env-file`, `--lark-cli`, `--content-language`, `--library-language`) go **before** the command.

```powershell
python ./dist/codex/plugins/paper2lark/scripts/paper2lark.py config init
python ./dist/codex/plugins/paper2lark/scripts/paper2lark.py state init
python ./dist/codex/plugins/paper2lark/scripts/paper2lark.py config show
```

`config init` creates default TOML only when absent; it does not overwrite custom settings or persist transient language overrides. `state init` creates schema-v3 or migrates a validated v1/v2 database after making a byte-for-byte backup. It also completes and backs up an early M4 schema-v3 database that lacks active-run reservations. Schema-v3 adds the publication operation journal, verified note baselines, and one durable active persistent-reading run per paper. Edit the private TOML to customize profiles. For Chinese notes and library labels, set `profiles.personal.language.content` and `.library` to `"zh-CN"`; keep `.keywords = "en"`. Keyword limits remain at most three words and eight labels. New records default to the configured unread status.

Configuration priority: command arguments → process environment → explicit environment file → selected profile → defaults. The environment-file format is deliberately small: allowlisted `KEY=value` lines, optional paired quotes and full-line comments; no interpolation or shell execution. Credentials are never Paper2Lark settings.

Supported variables:

```text
PAPER2LARK_HOME
PAPER2LARK_PROFILE
PAPER2LARK_CONTENT_LANGUAGE
PAPER2LARK_LIBRARY_LANGUAGE
PAPER2LARK_WIKI_URL
PAPER2LARK_BASE_URL
PAPER2LARK_TABLE_ID
PAPER2LARK_TEMPLATE_URL
PAPER2LARK_LARK_CLI
```

Resource overrides must supply Wiki, Base, table and template together. They do not overwrite saved configuration or silently reuse an old binding. The CLI override is an absolute native executable path, never a shell command. On Windows, the supported npm wrapper layout is resolved to its adjacent native `lark-cli.exe`.

## Bind an existing library

Configure `lark-cli` using its own account setup first. Paper2Lark reuses it and never starts login/logout automatically.

```powershell
python ./dist/codex/plugins/paper2lark/scripts/paper2lark.py bind --wiki-url "https://YOUR-HOST/wiki/NOTES-PARENT" --base-url "https://YOUR-HOST/wiki/INDEX-NODE" --table-id "tblYOURTABLE" --template-url "https://YOUR-HOST/wiki/TEMPLATE-NODE"
```

Replace the placeholders with real URLs and tokens. `--wiki-url` identifies the parent where future notes will be stored. Direct `/base/` index URLs and `/docx/` template URLs are also supported. The view in an index URL does not restrict field inspection.

Binding verifies the current account, resolved resources, readable template and complete field listing, then atomically writes `profiles/<profile>/bindings.json` locally. It makes **no remote changes**. A partial field mapping is valid for read-only inspection and reports missing workflow fields. Known English/Chinese labels map automatically; custom or duplicate names need explicit field IDs:

```json
{"title":"fldEXAMPLE","reading_status":"fldSTATUS"}
```

Pass that file with `--field-map-file`; use `--status-map-file` for mappings such as `{"unread":"To Read","read":"Read"}`. `--replace` explicitly authorizes replacing an existing local target/account binding. Normal refresh retains existing field-ID mappings; schema drift stops before replacing the stored binding.

## Manage the paper index

The examples below use synthetic identifiers. Put global options before `papers`. List and every preview are read-only; add `--apply` only for an authorized mutation. Apply requires schema-v3 state.

Collect a paper without reading it:

```json
{
  "schema_version": 1,
  "source": {"kind": "doi", "value": "10.1000/example"},
  "metadata": {
    "title": "Synthetic Paper",
    "authors": "Example Author",
    "year": 2026,
    "venue": "Example Venue"
  },
  "priority": "High",
  "keyword_proposal": {
    "selected_existing": ["AI Agents"],
    "proposed_new": []
  }
}
```

```powershell
python ./dist/codex/plugins/paper2lark/scripts/paper2lark.py papers add --input ./add.json
python ./dist/codex/plugins/paper2lark/scripts/paper2lark.py papers add --input ./add.json --apply
```

Identity reuse is based on canonical DOI, arXiv identifier, normalized HTTP(S) URL, or the exact hash of a local file/supplied text. Title is never a merge key. Existing nonempty title, keywords, status and priority are preserved; collection fills only empty metadata. Conflicting aliases stop with `IDENTITY_CONFLICT` instead of guessing. A repeated input returns `noop` when nothing needs to change.

List or search with intersection filters:

```json
{
  "schema_version": 1,
  "filters": {
    "statuses": ["unread"],
    "priorities": ["High"],
    "keywords": ["AI Agents"],
    "year_from": 2020,
    "year_to": 2026,
    "text": "agent"
  },
  "limit": 100,
  "include_vocabulary": true
}
```

```powershell
python ./dist/codex/plugins/paper2lark/scripts/paper2lark.py papers list --query ./query.json
```

The optional filters also accept `record_ids`. Status keys come from the saved mapping; priorities and keywords must exactly match live canonical options. Listing never creates state or changes a record.

Explicitly update one or more supported fields:

```json
{
  "schema_version": 1,
  "record_id": "recSYNTHETIC",
  "changes": {
    "reading_status": "read",
    "priority": "Low",
    "keywords": {
      "selected_existing": ["AI Agents"],
      "proposed_new": []
    }
  }
}
```

```powershell
python ./dist/codex/plugins/paper2lark/scripts/paper2lark.py papers update --input ./update.json
python ./dist/codex/plugins/paper2lark/scripts/paper2lark.py papers update --input ./update.json --apply
```

Only fields present in `changes` are considered. A keyword update replaces the complete selected set; omission leaves keywords unchanged. Reuse the live English vocabulary first. Every label is ASCII, has at most three words, and each paper has at most eight labels. New options require an evidence-bearing `proposed_new` entry and are reconciled against a refreshed live field before mutation.

## Read and optionally publish a paper

The read skill coordinates the host model and the deterministic runtime. Optional `sci-extract`, `research-paper-review`, and `arxiv2agent` skills can improve acquisition or review when they are already available, but they are not installed, bundled, or required. The fallback uses host-native reading plus Paper2Lark's standard library source ingestion and validation.

A draft-only request uses `persist_to_library: false`, performs no Base/Wiki mutation, and cannot later be published. A read-and-save request first collects the paper through the add workflow, then prepares reading from its exact record ID with `persist_to_library: true`. Both paths produce a verified local draft; only the second owns publication intent.

Prepare a run with one source or record:

```json
{
  "schema_version": 1,
  "persist_to_library": false,
  "requested_depth": "full",
  "reader_preference": "auto",
  "force_reread": false,
  "record_id": "recSYNTHETIC"
}
```

```powershell
python ./dist/codex/plugins/paper2lark/scripts/paper2lark.py read prepare --input ./read-request.json
```

`prepare` verifies the bound account, reads the selected record when present, snapshots the current template and complete live keyword vocabulary, and creates `<PAPER2LARK_HOME>/runs/<run-id>/handoff.json`. It performs no remote mutation. The handoff is self-contained for the host turn: it includes the run and paper IDs, every artifact path, exact host-written artifact fields, observed template selectors, language, vocabulary, and requested depth. A tracked Base record reuses its existing local paper UUID; otherwise the same library/source identity receives a stable draft UUID.

After obtaining the paper, describe the local UTF-8 text, abstract, or PDF in a source input and ingest it:

```json
{
  "schema_version": 1,
  "kind": "pdf",
  "path": "C:/absolute/path/paper.pdf",
  "original_location": "https://example.org/paper.pdf",
  "metadata": {}
}
```

```powershell
python ./dist/codex/plugins/paper2lark/scripts/paper2lark.py sources ingest --run RUN-ID --input ./source-input.json
```

The built-in PDF fallback handles bounded, unencrypted text PDFs with classic page/content streams and common Flate compression. It follows the PDF `/Pages` tree before emitting page locators; when page order cannot be established, it emits object locators plus `PDF_PAGE_ORDER_UNVERIFIED`. Scanned, encrypted, font-encoded, or structurally complex PDFs may require host/OCR extraction. Inputs with more than 10,000 sections/pages fail before any source artifact is copied. Unsupported input returns a clear error instead of a false full-read claim. Abstract-only input is preserved as abstract-only, with main-text coverage unavailable even if a caller supplies a stronger inspection claim.

The host writes `analysis.json`, `note-plan.json`, and `role-map.json` from the handoff and source bundle. Submit and inspect the result:

```powershell
python ./dist/codex/plugins/paper2lark/scripts/paper2lark.py read submit --run RUN-ID --analysis ./analysis.json --note-plan ./note-plan.json --roles ./role-map.json
python ./dist/codex/plugins/paper2lark/scripts/paper2lark.py runs show --run RUN-ID
```

Submission rejects stale or reordered template maps, unresolved evidence, coverage-state substitutions, human-owned template writes, malformed note blocks, and invalid keyword proposals. Human markers protect their complete template section without treating phrases such as “Manual Evaluation” or “人工智能” as personal content. Keywords remain English, reuse live options first, contain at most three words each, and total at most eight. A hash-bound `submitting` manifest state makes a retry reuse the first valid UTC generation timestamp after an interrupted final write. A successful result includes `draft.md`, `verification.json`, actual coverage, warnings, canonical keywords, template revision, and source hashes under the private run directory. Run artifacts are write-once; `runs show` rechecks every recorded path, size and SHA-256 before returning success.

For an authorized read-and-save run, create and inspect an immutable plan, then apply that exact file:

```powershell
python ./dist/codex/plugins/paper2lark/scripts/paper2lark.py publish plan --run RUN-ID
python ./dist/codex/plugins/paper2lark/scripts/paper2lark.py publish apply --run RUN-ID --plan ./publication-plan.json
python ./dist/codex/plugins/paper2lark/scripts/paper2lark.py runs resume --run RUN-ID
python ./dist/codex/plugins/paper2lark/scripts/paper2lark.py runs cancel --run RUN-ID
```

`publish plan` is remote read-only. It freezes the binding and artifact hashes, rechecks the template/record/vocabulary, snapshots the intended Wiki parent's children, and rejects an incomplete full read. `publish apply` journals intent before each write, creates and verifies a separate note revision, then updates the note link, summary, eligible keywords, and optionally reading status. Existing notes are never replaced in place because the tested Lark revision flag is not compare-and-swap.

If a document response is lost, the run enters `uncertain_remote_commit`; repeat the same apply command so recovery can inspect newly observed children and adopt exactly one note matching the run, record and source markers and the complete publication digest. It never blindly creates another document. Persistent reading reserves one active run per paper; an `ACTIVE_RUN_EXISTS` response identifies the run to resume. A changed note link or summary produces `blocked_conflict`; changed status or keywords are preserved and reported. `completed` is idempotent and does not create a duplicate note or timestamp. `runs cancel` is an explicit local abandonment action: it retains every artifact and releases the reservation without contacting Lark. If publishing had started, the result reports that remote state may remain.

## Diagnose without repair

```powershell
python ./dist/codex/plugins/paper2lark/scripts/paper2lark.py doctor --offline
python ./dist/codex/plugins/paper2lark/scripts/paper2lark.py doctor
python ./dist/codex/plugins/paper2lark/scripts/paper2lark.py doctor --verify
```

Offline mode checks local configuration/state/bindings without invoking Lark. Normal mode checks existing credential availability and, when bound, reads the library's resources and schema. Verify mode additionally permits ordinary CLI server verification/token refresh; it never starts a new OAuth flow.

Doctor never initializes state, rewrites bindings, creates notes, changes fields or resets authorization. SQLite with a pending WAL may be reported unsafe to inspect instead of creating auxiliary files. Exit codes: `0` for successful command/diagnosis without errors, `1` for diagnostic findings with errors, `2` for a rejected command or operational failure. Warnings are separate from errors; an empty unconfigured home is not itself a broken installation.

`CREDENTIALS_UNAVAILABLE` is deliberately distinct from `LOGIN_REQUIRED`. A sandbox `token_missing` response does not prove that the user needs to authorize again. Compare the same read-only command in a normal terminal before changing authentication. Account mismatches and incompatible field changes require an explicit follow-up action.

## Implementation and limits

- `src/paper2lark`: configuration, SQLite identity/publication state, typed Lark adapters, paper-index services, source ingestion, reading runs, publication and recovery.
- `skill_sources`: shared English skill instructions; generated per-host entrypoints.
- `scripts`: portable launcher and explicit-allowlist builder.
- `tests`: real filesystem/subprocess tests plus provider fault injection.
- `assets/keywords.en.json`: seed vocabulary, never a replacement for live options.

New local files/directories use restrictive POSIX modes where supported. Windows uses inherited ACLs; choose a private home directory. Private resource IDs, bindings, note data and raw integration evidence do not belong in the public repository. Local hashes detect accidental runtime changes; they are not cryptographic publisher signatures.

M2 index writes, M3 reading, and M4 publication/recovery use a real subprocess boundary with a synthetic provider. Existing-library live acceptance remains read-only; production Wiki/Base writes were not executed for M4. M4 does not ingest figure/table assets, provision a library, certify scanned-PDF OCR, support global multi-machine exactly-once delivery, or perform concurrent in-place document editing.

See the [design](docs/superpowers/specs/2026-09-13-paper2lark-design.md), [M0 findings](docs/compatibility-m0.md), [M1 validation report](docs/compatibility-m1.md), [M2 validation report](docs/compatibility-m2.md), [M3 validation report](docs/compatibility-m3.md), and [M4 validation report](docs/compatibility-m4.md). The M0 revision finding is why M4 publishes a separate note revision.

## License

Paper2Lark is released under the [MIT License](LICENSE).
