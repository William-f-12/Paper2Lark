# Paper2Lark: Cross-Host Product and Technical Design

**Revision:** 0.2, 2026-09-14<br>
**Status:** Approved implementation baseline; milestone amendments record shipped behavior.<br>
**Audience:** Implementers and reviewers who have not read the planning conversation.<br>
**Primary targets:** Claude Code and local Codex environments with shell and filesystem tools.<br>
**Existing inputs:** [Library conventions](../../library-conventions.md), [keyword seed](../../../assets/keywords.en.json).

## 1. Objective and scope

Paper2Lark turns a user's Lark Wiki and Base into a configurable paper collection and reading workflow. Users collect papers, request model-assisted reading, publish evidence-linked notes using their current template, and maintain the index without losing their own edits.

The same source repository must produce working Claude Code and Codex plugins. The host model performs reasoning; a shared Python runtime performs deterministic validation, persistence, and Lark operations through the user's installed `lark-cli`.

### 1.1 Release requirements

| ID | Requirement |
| --- | --- |
| R01 | Installable plugin packages for Claude Code and Codex share the same core implementation. |
| R02 | Create a new Wiki/index/template, or bind an existing library without rebuilding it. |
| R03 | Support DOI, arXiv identifiers/URLs, publisher/direct-paper URLs, local PDF, and supplied text. |
| R04 | Add and deduplicate records before a note exists; preserve source identity and the version actually read. |
| R05 | Read papers using available optional skills, with a built-in fallback when those skills are absent. |
| R06 | Follow the user's current Lark template, including review-paper variants and protected human areas. |
| R07 | Enforce English keyword shape, at most three words per keyword and eight distinct keywords per paper; reuse live options first. |
| R08 | Publish a note, verify it, and update its existing index record; recover partial failures. |
| R09 | Preserve manual edits, user-owned fields, document links, and customized resource structure. |
| R10 | Search/list papers and explicitly change status, priority, and keywords. |
| R11 | Diagnose authentication, credential-store access, network, capability, resource, and permission failures separately. |
| R12 | Share configuration and durable state between hosts on the same machine; survive plugin upgrades. |
| R13 | Use English for project code/docs/skill instructions, configurable note/library languages, and English keywords. |
| R14 | Keep credentials and private library data outside the distributable package. |
| R15 | Make coverage, uncertainty, AI analysis, personal reading, and reproduction status distinguishable. |
| R16 | Keep the plugin lightweight: use the standard library where practical, reuse the host model and installed `lark-cli`, and keep optional readers outside the core package. |

### 1.2 Non-goals for the first public release

No hosted service, autonomous scheduler, vector database, multi-tenant SaaS, cross-machine distributed lock, automatic paper reproduction, automatic deletion/merging of existing notes, or non-Lark storage backend. No requirement for a separate model API key. No claim of support for the Claude consumer web app, Claude Desktop extension format, or remote hosts that cannot run the local runtime and reach the user's CLI credentials.

Research retrieval, topic synthesis, and cross-paper reasoning are later features. The first release preserves the identifiers, provenance, and relationships that those features would need.

## 2. Decisions and their rationale

| Decision | Rationale | Rejected alternative |
| --- | --- | --- |
| One shared runtime, two generated host packages | Host conventions can evolve without duplicating business rules. | Independently maintained Claude/Codex implementations. |
| Skills plus local CLI, no required MCP server | Existing local Lark authentication and ordinary shell tools are sufficient. | A persistent service and another authentication layer. |
| Explicit model handoff artifacts | The Python process cannot directly execute a host skill or summon the model. | A fictitious `read()` command that silently calls a model provider. |
| SQLite for durable control state; files for large artifacts | Two hosts need transactionally consistent runs and mappings. SQLite is in Python's standard library. | Several mutable JSON registries requiring a custom transaction protocol. |
| Native Lark template plus local revision snapshot | Customization remains in the user's normal editing environment. | A fixed template embedded in every skill. |
| Read/compare/update with conservative conflict handling | Existing collaborative notes contain human edits and resource blocks. | Whole-document overwrites on every reread. |
| Live Base options are authoritative | User vocabulary changes must take effect immediately. | Resetting the field to the shipped 45-word seed. |
| Capability-based CLI checks plus recorded versions | A version number alone does not establish command or output compatibility. | Automatically updating lark-cli whenever an operation fails. |
| Dependency and package-size budgets | Small packages install quickly, reduce supply-chain surface, and consume less host context. | Bundling SDKs, optional readers, duplicated references, or a daemon into the core plugin. |

The SQLite choice refines the earlier JSON-only sketch. Configuration and reviewable model artifacts remain TOML/JSON; SQLite is the local coordination and recovery ledger, not a second remote paper database.

## 3. Host compatibility and distribution

### 3.1 Verified platform facts

Claude Code documents a `.claude-plugin/plugin.json` manifest, skills under the plugin root, and plugin-qualified skill invocation. Its install directory can change during updates; persistent data should not be written there. [Claude Code plugin reference](https://code.claude.com/docs/en/plugins-reference)

Codex supports `SKILL.md` skills with name/description and optional supporting files. [OpenAI skill documentation](https://learn.chatgpt.com/docs/build-skills)

Current OpenAI documentation describes portable root `plugin.json` packages while retaining `.codex-plugin/plugin.json` compatibility packages. The local plugin-creator also generates that compatibility layout. V0.1 deliberately targets the compatibility layout and validates it in the target Codex installation; a portable-manifest migration is a separate packaging change. [OpenAI plugin packaging](https://developers.openai.com/plugins/build/plugins)

Local observations during design: Codex CLI `0.154.0-alpha.6.2`, Claude Code `2.1.270`, lark-cli `1.0.89`, and Python `3.13.5` are present. These are observed versions, not a claim that Paper2Lark has already passed integration tests on them.

### 3.2 Source and generated packages

Canonical workflow instructions live in `skill_sources/`. A small packaging tool combines a shared workflow body with a host-specific launch/invocation appendix. Business rules must never live exclusively in an appendix.

Generated packages:

```text
dist/
  claude/
    .claude-plugin/marketplace.json
    plugins/paper2lark/
      .claude-plugin/plugin.json
      skills/{setup,add,read,library,doctor}/SKILL.md
      references/
      scripts/{bootstrap.py,paper2lark.py}
      wheelhouse/
      assets/
      build-info.json
  codex/
    .agents/plugins/marketplace.json
    plugins/paper2lark/
      .codex-plugin/plugin.json
      skills/paper2lark-{setup,add,read,library,doctor}/SKILL.md
      references/
      scripts/{bootstrap.py,paper2lark.py}
      wheelhouse/
      assets/
      build-info.json
```

Both plugin folders are named `paper2lark`, and both manifests use `paper2lark` as the plugin identity. Workflow IDs are `setup`, `add`, `read`, `library`, and `doctor`; their exposed skill names can differ by host. The Claude examples are `/paper2lark:add` and `/paper2lark:read`. Codex instructions use the installed skill selector or names such as `paper2lark-add`, rather than claiming Claude slash commands work unchanged.

V0.1 produces two downloadable release archives, each containing a complete local marketplace root. This is a concrete public distribution route and does not depend on approval by an official marketplace. Install documentation covers archive extraction, adding that local marketplace, and installing the plugin in the host. A Git-hosted marketplace can later publish the same generated tree. Do not present a nonexistent repository URL as an installable source. Claude marketplace manifests and install commands must be validated using its own tooling. [Claude marketplace documentation](https://code.claude.com/docs/en/plugin-marketplaces)

Every package includes its own runtime wheel, dependencies, references, and assets. No symlink or `../../` dependency may escape the installed plugin. Build tests compare the runtime wheel hashes across the two packages.

The initial packages contain no required hooks, custom subagents, app connectors, or MCP configuration. Manifest fields are emitted only for components actually included. Shared skill frontmatter uses `name` and `description`; any additional host-specific metadata is generated and validated separately. Cross-host compatibility must not depend on a host-specific `Skill` tool name or on an auto-loaded project instruction file.

### 3.3 Runtime bootstrap

Prerequisites are an installed local host, a configured `lark-cli`, and Python 3.11 or later with `venv`/`pip` support. Release validation initially covers Python 3.11, 3.12, and 3.13. `uv` may be supported as a convenience; it is not required.

M0 through M2 have no third-party Python dependency. They use the standard library, including `sqlite3`, `tomllib`, `argparse`, `hashlib`, and `subprocess`, plus the user's installed `lark-cli`. The existing native lock and deterministic TOML writer remain in the core instead of adding `filelock` or `tomli-w`. M3 may add one pinned PDF parser only if the built-in fallback cannot meet its acceptance gate within the release-size budget; optional reading skills remain external and are never redistributed.

The sum of files inside either generated M2 plugin directory must remain at or below 256 KiB. The first public core release targets at most 1 MiB per host before marketplace metadata; a larger package requires an explicit design amendment with measured benefit. Each workflow `SKILL.md` targets fewer than 500 English words. Build output records runtime and package byte counts, and package tests enforce the active milestone budget. Tests, development plans, compatibility evidence, caches, private data, and optional reader implementations are excluded by the build allowlist.

`scripts/bootstrap.py` uses the selected Python interpreter to create an external, versioned virtual environment and installs from the bundled wheelhouse with network lookup disabled. It never installs optional reading skills or changes the user's lark-cli installation. `scripts/paper2lark.py` is a standard-library launcher that resolves its own location and invokes the matching environment. Runtime and package build IDs must agree; a stale globally installed executable is not used silently.

Claude's adapter can locate the launcher using its documented plugin root variable. Codex's adapter resolves the launcher relative to the actual loaded skill directory, then invokes the absolute path. Neither assumes the shell starts in the plugin directory. Host-specific instructions explain that paths must be shell-quoted; user input is then passed to the runtime as structured files, never interpolated into shell commands.

### 3.4 State ownership and upgrade behavior

Default persistent root is `~/.paper2lark/`, overridable by an absolute `PAPER2LARK_HOME`. It is deliberately independent of `.claude`, `.codex`, and plugin caches so both hosts see the same data.

```text
~/.paper2lark/
  config.toml
  profiles/<profile>/bindings.json
  state.sqlite3
  locks/
  runtimes/<build-id>/
  runs/<run-id>/
    request.json
    template.json
    source.json
    analysis.json
    note-plan.json
    draft.xml
    verification.json
    assets/
  baselines/<library-id>/<paper-uid>/
  logs/
```

Files with source material, personal notes, bindings, or run details remain private local data. Public fixtures are synthetic or explicitly redacted. Uninstalling a plugin does not delete this root. Cleanup is an explicit action with a preview and must retain human-protection baselines by default.

Schema migrations run under a migration lock with a database backup. Read-only inspection of a newer unsupported schema is allowed only when safe; writes fail with `STATE_SCHEMA_UNSUPPORTED`. A running operation records its runtime build ID. Resume first locates that build, or performs an explicitly supported artifact migration; it does not reinterpret old artifacts silently.

## 4. User-facing skills

| Workflow | Inputs | Result | Invocation boundary |
| --- | --- | --- | --- |
| setup | Create intent, or existing Wiki/Base/template URLs | Validated profile and resource bindings | Provision only when the request authorizes resource creation. |
| add | Identifier, URL, local file, or paper text; optional priority | New or existing index record | Collect metadata only; do not turn a save-for-later request into full reading. |
| read | Source or record; depth; optional analysis preference | Note, index link, summary, coverage, and run status | Reuse `add` if no record exists. A request to read without saving can stop at the local draft. |
| library | Query or explicit field changes | Filtered records or a verified update | Retrieval requests never change reading status. |
| doctor | Profile or run | Diagnostic findings, or narrowly scoped recovery | Default is read-only; repair applies only to authorized affected operations. |

Skill descriptions explain positive and negative triggers in English. The short entrypoint loads only the required references: setup, add, reading, publication, or recovery. Shared instructions are not pasted in full into all five skills.

Side-effect authorization follows the user's request. An explicit request to add/read-and-save authorizes its ordinary writes; do not ask the user to reconfirm every field. Ask only for a real ambiguity, new scope, unresolved conflict requiring a choice, or a host/CLI approval boundary that cannot be satisfied by existing authorization. The plugin never grants itself additional host permissions.

## 5. Configuration and resource binding

### 5.1 Configuration precedence

Operation arguments override supported `PAPER2LARK_*` environment variables, which override the selected profile, which overrides defaults. Transient overrides do not rewrite saved settings. Resource overrides must be resolved and validated as a complete target before use; a Wiki override cannot silently leave the old Base binding active.

Do not load `.env` from an arbitrary current working directory automatically. An explicit `--env-file` may load the documented Paper2Lark keys. Lark credentials are not Paper2Lark configuration values.

Proposed keys:

```toml
schema_version = 1
default_profile = "personal"

[profiles.personal.language]
content = "zh-CN"
library = "zh-CN"
keywords = "en"

[profiles.personal.reading]
depth = "full"
analysis = "auto"
optional_skills = "auto"

[profiles.personal.workflow]
mark_read_after_publish = true
existing_note = "preserve_manual"
archive_strategy = "fixed_parent"

[profiles.personal.keywords]
max_words = 3
max_per_paper = 8
reuse_existing_first = true

[profiles.personal.lark]
identity = "user"
```

The public default `mark_read_after_publish` is `false`; the personal profile can use `true`, matching the existing workflow. In either case, never mark a paper personally reproduced as a consequence of generation. `language.library` affects newly provisioned labels and templates only. Keywords remain English; a configuration attempting to raise either keyword limit is invalid for v0.1. Smaller limits are permitted.

Documented environment overrides: `PAPER2LARK_HOME`, `PAPER2LARK_PROFILE`, `PAPER2LARK_CONTENT_LANGUAGE`, `PAPER2LARK_LIBRARY_LANGUAGE`, `PAPER2LARK_WIKI_URL`, `PAPER2LARK_BASE_URL`, `PAPER2LARK_TABLE_ID`, `PAPER2LARK_TEMPLATE_URL`, and `PAPER2LARK_LARK_CLI`. The CLI path override is an executable location, not a shell command string.

### 5.2 Binding model

`LibraryBinding` contains a schema version, an account fingerprint, Wiki space, notes parent, Base token, table ID, template location, logical-field mappings, logical-status mappings, and the last inspected schema digest. It also stores original user URLs for display.

Account fingerprint includes the effective CLI identity, app ID, user ID, and brand when exposed. Resource identity is based on resolved tokens, not host name spelling alone. `library_id` is derived from the physical Base/table pair so two profiles pointing at the same table coordinate writes. Verify the effective account before a mutation; if it changed, stop with `ACCOUNT_MISMATCH` rather than switching identities or rebinding automatically.

URLs are resolved through the CLI. A `/wiki/` token is not a `space_id`, and an index URL's view filter must not limit deduplication to that view. Only trim surrounding whitespace or obvious trailing sentence punctuation after validating the remaining URL; never reconstruct OAuth verification URLs.

### 5.3 Logical index schema

| Logical key | Current personal label | Type | Ownership |
| --- | --- | --- | --- |
| title | 标题 | text | Fill on collection; do not replace a user's curated title during routine refresh. |
| authors | 作者 | text | Source-derived; fill missing values. |
| year | 年份 | number | Source-derived, retaining publication/version ambiguity in the note. |
| venue | 发表场所 | text | Source-derived; preserve existing annotations. |
| source_url | 原文链接 | URL-style text | Required when a remote source is known; never publish a local file path. |
| paper_key | 论文标识 | text | Normalized DOI/arXiv identity when established. |
| keywords | 关键词 | multi-select | Model proposal plus deterministic constraints. |
| summary | 一句话结论 | text | Generated after reading; protect edits made since the last plugin write. |
| note_url | 笔记文档 | URL-style text | Verified target note, not an unverified draft link. |
| reading_status | 状态 | single-select | User-driven or the explicitly configured completion behavior. |
| priority | 优先级 | single-select | User-owned; initialize only on collection. |
| added_at | 加入时间 | created-at | Server-owned; never write. |

New libraries receive all 12 fields. Existing-library inspection can bind a subset for read-only queries, but a write workflow reports the missing fields it actually needs. The minimum complete add/read workflow requires title, source URL, paper key, keywords, reading status, and note URL. Setup can add missing fields after the user requests migration; it does not convert incompatible field types or replace unrelated options automatically.

Mappings store field IDs and refresh current names/types before writes. Duplicate names require IDs. Human renames are harmless if the ID/type remain compatible. Deleted or type-changed fields produce `SCHEMA_DRIFT` with an exact remapping need. Existing extra fields, formulas, views, and columns remain intact.

## 6. Canonical data contracts

Use versioned JSON artifacts and Python dataclasses with explicit validation. Export JSON Schemas from the declared contracts and check them in CI. Do not rely on a model producing well-formed JSON without validation.

### 6.1 Paper identity

`PaperIdentity` contains:

- `paper_uid`: immutable local UUID assigned when first tracked.
- `canonical_key`: established `doi:<lowercase DOI>` or `arxiv:<base ID>`, or null.
- `aliases`: other verified identifiers and normalized source URLs, each with provenance.
- `source_fingerprint`: SHA-256 of supplied bytes for local-file exact matching.
- `record_id`: the remote Base record once known.
- `source_version`: the version actually read, independently of paper identity.

A local UUID is not a fabricated DOI and does not get written into the user's paper identifier field. A file hash establishes identical bytes, not equivalence between revised PDFs. Title similarity produces candidates rather than an automatic merge. On adding a DOI later, preserve the UUID and existing aliases; change the displayed canonical key only when the identity is verified. Local aliases are a cache, not a cross-machine uniqueness guarantee.

### 6.2 SourceBundle

Required fields: `schema_version`, `paper_uid`, `metadata`, `sources`, `sections`, `assets`, `coverage`, and `warnings`.

Every source has `source_id`, original/retrieved location, retrieval time, content hash, content type, and version. Every section has a stable bundle-local ID, source ID, heading, text path, locator, and appendix flag. Asset records carry source position, caption, local path, content hash, and extraction/inspection status.

Coverage records main text, appendix, figures, tables, and supplementary materials separately using `complete`, `partial`, `unavailable`, or `not_applicable`, plus a reason. Text-PDF extraction alone does not establish visual inspection. Model reading coverage is an explicit attestation against the available bundle, not something the runtime can prove from token counts.

### 6.3 AnalysisBundle

Required fields: `schema_version`, `run_id`, `paper_uid`, `paper_type`, `requested_depth`, `actual_coverage`, `takeaway`, `claims`, `ai_analysis`, and `keyword_proposal`.

Each claim has a bundle-local ID, a type (`author_claim`, `reported_result`, or `external_context`), text, and evidence references. An evidence reference identifies a source and a section/page/figure/table locator. The locator must resolve in the source manifest; its presence does not prove the interpretation is correct. The host checks that numerical claims and source passages actually agree before publication.

`keyword_proposal` separates selected existing labels from proposed new labels. Each new label contains its intended concept, why current options are inadequate, and the related existing options considered. The script validates the shape and limits; the host remains responsible for semantic suitability and English terminology.

### 6.4 TemplateSnapshot and NotePlan

`TemplateSnapshot` stores the template document ID, native revision, content digest, raw structured content, and compiled section roles. A role includes its position, heading, ownership (`ai`, `human`, or `mixed`), instructions, paper-type applicability, and source block IDs.

`NotePlan` is an ordered list of typed sections and blocks linked to template roles. Supported initial block kinds: heading, paragraph with inline formatting/links, list, simple table, callout, equation, and image reference. A provenance section includes source/version, coverage, generation date, template revision, and the index record link. The runtime injects binding-owned links itself.

`RoleMap` contains `schema_version`, the exact template digest/revision, and section entries with observed block selectors, semantic roles, ownership, variant applicability, and extracted generation instructions. Every selector must resolve in the snapshot. It cannot grant write ownership to a block explicitly marked human-only. The host supplies this interpretation when there is no valid cached map; the runtime validates the references and protection invariants.

Do not accept arbitrary shell, executable snippets, Lark mutation commands, or a replacement library target inside these artifacts. A code listing from a paper is display content, never a command to run.

### 6.5 PublicationBaseline

After successful read-back, store the actual remote blocks and their normalized hashes, semantic role associations, managed field values, remote revision, and the source/runtime/template fingerprints. Save the server's representation, not just the submitted XML: Lark may normalize links and block structure.

Hash normalization ignores volatile server IDs when comparing content, but preserves meaningful text, links, formatting, resource references, and child order. If a block type cannot be faithfully normalized, treat it as protected. Never use a plain-text-only hash to decide whether a rich block is unchanged.

### 6.6 Local database model

| Table | Key / constraint | Stored data |
| --- | --- | --- |
| libraries | `library_id` primary key; physical Base/table pair unique | Current binding digest and non-secret resource coordinates. |
| papers | `paper_uid` primary key; `(library_id, record_id)` unique when a record exists | Canonical identity, latest verified note reference, source/version summary. |
| aliases | `(library_id, alias_kind, normalized_value)` unique | Paper UUID, verification source, and confidence category. Only verified aliases enter this table. |
| runs | `run_id` primary key | Paper/library, intent, requested/actual depth, status, last completed stage, runtime build, input fingerprints. |
| active_runs | `(library_id, paper_uid)` primary key; `run_id` unique | Durable reservation across host/model turns. |
| operations | `operation_id` primary key; `(run_id, sequence)` unique | Allowlisted operation kind, target, preconditions, request digest, before snapshot path, outcome, remote IDs. |
| artifacts | `(run_id, artifact_kind, revision)` unique | Relative path, hash, contract version, origin, creation time. |
| baselines | `(library_id, paper_uid, revision)` unique | Verified remote revision, block/field snapshot paths, latest-baseline flag. |

Proposed new aliases that collide with another paper are rejected for review, not reassigned. Identity resolution and initial reservation run under a library lock so simultaneous local collection cannot allocate competing papers for the same known alias. The lock is released before a model handoff. Another machine is outside this coordination boundary.

Run fingerprints include source hashes/version, template digest, note language, requested depth, analysis selection, and policy version. A repeated invocation can offer a verified existing result; an explicit reread creates a new analysis run against the same paper/record. Retrying or resuming a run keeps its run ID and operation journal. An unchanged verified publication is a no-op, not a new generation timestamp written to the note.

### 6.7 Request ownership

`ReadRequest` contains source/record selection, profile, `persist_to_library`, requested depth, optional reader preference, and a `force_reread` boolean. `persist_to_library` is resolved from the user's intent before preparation. For a draft-only request it is false: no record, keyword option, status, or note is created or updated. A draft may use the configured template and vocabulary read-only, and its index link stays absent until publication is requested.

`AddRequest` contains source selection, supplied metadata, optional priority, and a validated keyword proposal when one is available. Metadata-only collection may leave keywords empty until enough content is available to tag accurately. It must not fill eight speculative labels merely to complete a form.

`PublicationPlan` carries the authorized intent, immutable target binding digest, source/template/artifact hashes, base document revision, expected record values, ordered mutations, and conflict decisions. A later request to publish a previous draft creates a new publication plan under that new intent. A model-authored analysis cannot flip the persistence flag or change the target.

## 7. Source acquisition and reading orchestration

### 7.1 Capability selection

The runtime detects executables and parsing capabilities. The host adapter reports skills it can actually load in the current session. Finding a directory on disk is not equivalent to having an invocable host skill.

| Input/task | Preferred route | Fallback |
| --- | --- | --- |
| arXiv source | Available `arxiv2agent`, if its requirements are met | Direct paper content through host tools or built-in download/text extraction. |
| DOI/publisher paper | Available sci-extract acquisition mode | Host resolves a reachable full text; runtime ingests it. |
| Local PDF | Baseline `pypdf` extraction and host visual inspection | Available OCR/reading tools; otherwise mark missing text/figures. |
| Supplied text | Direct ingestion with supplied-source provenance | No external lookup needed unless identification or requested context requires it. |
| Routine scientific reading | sci-extract when available and selected | Built-in research/review analysis instructions. |
| Critical review | research-paper-review when requested and available | Built-in evidence and limitation checks. |

An explicit user-selected reader takes precedence. If it is absent, explain the fallback rather than silently pretending to use it. Do not chain all optional skills for every paper. Do not install optional tools automatically merely because a skill's own setup section suggests it; use the user's requested optional-dependency policy.

External skill output is normalized to the same contracts. Skills may have different input formats, licensing, and output instructions. V0.1 uses optional integrations without redistributing their source. A separate model API must not become an implicit dependency of the default workflow.

### 7.2 Model handoff

1. `read prepare` resolves identity and target, snapshots the template and live keyword options, and creates a run. It finds/adds a record only when `persist_to_library` is true; draft-only mode remains remote read-only.
2. The command returns `awaiting_agent` with an artifact directory and a typed list of missing work, such as acquiring source bytes, interpreting a changed template, or filling `analysis.json`.
3. The host acquires content or uses an optional extractor, then calls `sources ingest` to validate/hash local artifacts and create `SourceBundle`. It reads the bundle and writes analysis, note plan, and a role map when required. Native host reading of a PDF can supplement its inspection status but cannot invent extracted source sections.
4. `read submit` parses those files, validates the role map against the snapshot, and produces a validation report and rendered draft. Its `--roles` argument is required when the template digest has no validated cached map. It does not run a hidden model call.
5. If the user requested publication, the skill invokes `publish apply` after all required checks pass. Otherwise it returns the local draft.

The host can pause between these steps. Each result contains enough persisted context to continue from a new task or another supported local host.

### 7.3 Coverage and depth

`quick` prioritizes the bibliographic record, takeaway, research question, inspected main idea, available evidence, and explicit gaps. `full` requests comprehensive reading but never overwrites the measured coverage. An abstract-only result cannot be published as a completed full reading; it can be saved as a clearly labeled quick overview or await full text. A full main-text read with unavailable appendix/figures is published only with the actual component coverage made explicit.

The baseline supports text-based PDFs. Scanned documents and complex mathematical/visual extraction depend on available host/OCR capabilities. Unsupported source acquisition returns an actionable need for the paper or a supported source; it does not fabricate a paper from a familiar title.

## 8. Controlled keyword workflow

The shipped seed is the existing 45-label English list. It initializes a new library and documents examples. It is never used to replace an existing customized vocabulary.

For each operation:

1. Read the complete current keyword field, with pagination if applicable.
2. Let the host match the paper's concepts to canonical options, considering synonyms and acronyms.
3. Validate selected labels against the fetched options. Normalize comparison whitespace/case, but write the existing canonical spelling.
4. For new proposals, reject blank labels, non-ASCII letters, sentences, delimiters representing multiple labels, duplicates, and word-count violations. Hyphen-separated English words count separately; acronyms count as one word. Proper technical English still needs model review.
5. Enforce the limit on the union of selected and proposed labels: no more than eight distinct labels.
6. Acquire the library mutation lock; refresh the field and reconcile proposals with any newly added options.
7. If new labels remain necessary, extend the complete current writable field definition. Preserve all options and unrelated properties. Never use the original seed as a replacement field body.
8. Confirm the expected labels exist, then write the record's labels.
9. Verify record labels and ensure the rendered note uses the same canonical set.

If the field uses an unsupported dynamic options source or incompatible configuration, return `KEYWORD_SCHEMA_UNSUPPORTED`. Do not convert it to a static field. A full-definition field update may race with a person or another machine; local locks cannot guarantee protection from such external writes. Detect drift where possible and fail conservatively rather than claiming a distributed transaction.

Historical records over the limit are not automatically trimmed during unrelated queries. An explicit retag operation proposes a compliant selection while preserving the original in the audit record.

## 9. Template interpretation and safe editing

### 9.1 Template contract

The native document is authoritative for structure, tone, and depth. Model interpretation maps custom headings and instructions to semantic roles without requiring English headings or fixed section numbers. Explicit human-only instructions always protect the affected blocks.

For bundled templates, roles are provided by a matching built-in mapping. For an existing template, setup returns a proposed role map. When clearly indicated by headings/instructions, it can be saved within the authorized bind operation. Ambiguous ownership blocks generation into that area; the host asks only about those sections or conservatively treats them as human-owned.

The snapshot digest, not just the document URL, identifies the compiled mapping. On a changed template, recompile the affected roles before generation. A run remains tied to its snapshot. If the live template changes before publication, report the change; the default is to regenerate using the new revision, unless the user explicitly chooses the recorded revision for that run.

The library supports a research-paper template, a review variant, and an explicitly requested quick overview. A review variant may be embedded as instructions in one document or configured as a separate template. User-created alternative templates need no code change if their required blocks and ownership can be represented safely.

### 9.2 Supported rendering

Render through a typed intermediate representation to a tested DocxXML subset. Escape text only, preserve deliberate links, and never use arbitrary model-generated XML as a trusted mutation payload. Images must refer to verified run assets. Equations use tested native representation when supported; otherwise retain source LaTeX with a clear rendering limitation, not a silently altered formula.

Unknown template resources such as embedded Base tables, synced blocks, whiteboards, or attachments are inventoried. Preserve existing ones in updates. New-note template copying must use a verified native-copy path when preservation is required; if neither safe copy nor supported rendering is available, stop with `UNSUPPORTED_TEMPLATE_RESOURCE` and retain the draft. Do not silently replace a resource with a fake image or empty placeholder.

### 9.3 Three-way comparison

For each managed section or field compare:

- B: the last verified plugin baseline;
- R: the current remote content;
- N: the proposed new content.

| Condition | Action |
| --- | --- |
| Human-owned area | Preserve R, regardless of B or N. |
| R equals B; N differs | Eligible for a targeted update. |
| N equals B; R differs | Preserve the user's remote change. |
| R equals N | No-op; an earlier attempt may already have applied it. |
| R and N both differ from B | Conflict: preserve R; produce an append-only update or request a localized choice. |
| No trustworthy B | Treat existing content as user-owned; append a clearly dated AI update. |

V0.1 defaults to a separately dated AI update section when any content conflict exists, rather than interleaving a partially refreshed analysis. The complete proposed update remains local for review. Protected human sections are never copied into the new AI section as generated prose.

Use document revision preconditions where the tested CLI supports them, and refresh block IDs after structural mutations. A revision mismatch triggers a new compare, not a forced write. Manual edits in a non-protected section are still protected by the baseline comparison. Deleting a heading, moving a section, or removing an old AI paragraph is a change, not permission to reconstruct it.

Blocks with human comments are protected even if their text matches the baseline. Preserve resource sidecars and comment anchors when deciding patch eligibility. If comment/resource metadata is truncated or cannot be resolved sufficiently to establish safe replacement, use append-only publication. The first release does not claim that rewriting an unchanged paragraph automatically preserves all attached collaboration metadata.

## 10. Publication and recovery

### 10.1 Runtime states

```text
prepared -> awaiting_source -> awaiting_agent -> validated
validated -> publishing_note -> note_verified -> updating_index -> completed

Any stage may produce:
  blocked_auth, blocked_environment, blocked_source,
  blocked_schema, blocked_conflict, failed_retryable,
  failed_terminal, uncertain_remote_commit, cancelled
```

Store the last completed stage separately from current status. Cancellation keeps existing remote resources and artifacts. A blocked run can resume after its dependency is resolved.

### 10.2 Journaled operation protocol

Every remote mutation follows:

1. Acquire the appropriate local mutation lock.
2. Read current remote preconditions and verify account/binding.
3. Commit an operation intent containing the target, before snapshot, desired digest, and recovery strategy to SQLite.
4. Execute the CLI call outside a long SQLite transaction.
5. Save the sanitized response and any known remote IDs immediately.
6. Read the relevant remote state until verified or a bounded deadline expires.
7. Mark the operation verified and advance the run in one database transaction.

Timeout or interruption between steps 4 and 5 is an uncertain outcome, not automatic failure. An `ok` response with warnings, ignored fields, or partial completion requires operation-specific verification.

### 10.3 New-note route

The preferred candidate is `docs +create` with complete XML and an explicit Wiki parent token, followed by Wiki resolution and content read-back. Local help exposes that route, but its placement, partial-failure, and returned-ID semantics must pass a disposable integration probe before the implementation relies on it.

The fallback is a journaled sequence that creates the Wiki node, immediately records its ID, then writes content. Do not infer that the compound create route is atomic.

Before creation, record the current child-node set under the explicit parent. The rendered content includes the actual index record link and paper source identity. Recovery searches newly observed candidates in the intended location and checks those references and the intended content. A matching verified document can be adopted. Title alone is insufficient.

If the result is an empty node, multiple candidates, an inaccessible node, or no conclusive candidate after an uncertain create, stop in `uncertain_remote_commit`. Do not automatically create another document just because a search returns no match. The user can select a candidate or explicitly authorize a fresh create after inspection.

### 10.4 Index completion

Only after note verification update note URL, summary, and other authorized fields. Re-read the record first. Use managed-field baselines to detect edits to summary or keywords; priority, annotations, and unrelated fields are not routine outputs.

When the configured completion policy changes reading status, do so only if the status still matches the run's last observed/plugin-written value. If the user changed it during reading, preserve that value and report it. A quick overview never silently marks the requested full-read workflow completed.

If index update succeeds but the response is lost, compare desired fields on resume and mark the step complete when they already match. If the note is verified but the index fails, retry only the missing index step.

### 10.5 Locks and limitations

Use per-library process locks for remote record/field mutations and a SQLite active-run reservation for a paper across model turns. Do not hold an OS process lock while waiting for the model or user. A second host encountering an active run offers to resume it rather than silently starting another publication. Recovery of a reservation is explicit; a time limit alone is not permission to duplicate remote work.

SQLite uses short transactions, foreign keys, a bounded busy timeout, and one state-schema migration lock. Filesystem artifacts are written to temporary files and atomically replaced on the same filesystem before their hash is committed.

The guarantee is cooperative same-machine coordination plus conservative remote reconciliation. It is not global exactly-once delivery: another machine, manual edits, or clients bypassing Paper2Lark can race. On a different machine, bind and rebuild mappings from remote records; treat notes without local baselines as append-only.

## 11. CLI contract

The examples below are proposed Paper2Lark interfaces, not commands already installed in this repository. The packaged launcher exposes the same interface as `paper2lark`.

### 11.1 Command families

| Command | Purpose | Remote effects |
| --- | --- | --- |
| `doctor --profile NAME` | Check runtime, CLI, identity, resources, mappings | Read-only. |
| `setup inspect --input request.json` | Resolve supplied resources and propose bindings | Read-only. |
| `setup apply --plan setup-plan.json` | Save bindings or create explicitly planned resources | Plan-defined writes. |
| `papers add --input paper.json --apply` | Collect/deduplicate a paper | Add or fill approved fields. |
| `papers list --query query.json` | Query by supported fields | Read-only. |
| `papers update --input changes.json --apply` | Explicit status/priority/keyword edits | Selected-field writes. |
| `read prepare --input request.json` | Create a run and model handoff | May reuse authorized collection; no note write. |
| `sources ingest --run RUN --input source-input.json` | Import PDFs/text/provider artifacts and construct a verified source manifest | Local artifacts only. |
| `read submit --run RUN --analysis analysis.json --note-plan note-plan.json [--roles role-map.json]` | Validate template interpretation, analysis, and note blocks; render | Local artifacts only. |
| `publish plan --run RUN` | Compare target, ownership and remote revisions | Read-only. |
| `publish apply --run RUN --plan publish-plan.json` | Publish and reconcile the planned changes | Journaled writes. |
| `runs show --run RUN` | Inspect status and recovery instructions | Read-only. |
| `runs resume --run RUN` | Inspect the last stage and return the next safe action | Read-only until an explicit apply step. |
| `runs cancel --run RUN` | Release the active reservation and retain artifacts | Local only. |
| `library rebind --plan binding-plan.json` | Adopt a verified remapping | Local only unless a separate migration is authorized. |

Plan/apply separation creates an inspectable change set; it is not a mandatory extra user confirmation. A skill can plan and apply within the same authorized request. Plans bind account, target, source artifacts, template revision, and remote preconditions. Modified or stale plans must be regenerated, not forced through with a flag.

`SourceInput` lists the actual local files, their declared source URLs/versions, extraction provider, metadata provenance, and inspected coverage. Paths must resolve within the run directory or an explicitly supplied source location; ingestion copies them into the run and computes hashes itself. An optional extractor's JSON is parsed through its adapter, not accepted as executable instructions.

If `papers add` needs semantic keyword selection or identity disambiguation, it returns a handoff before mutation. The host completes the AddRequest from the observed candidates/options and resubmits it. Empty keywords are valid when there is insufficient evidence; guessing labels is not required to create a pending record.

### 11.2 JSON response envelope

```json
{
  "protocol_version": 1,
  "status": "awaiting_agent",
  "operation": "read.prepare",
  "run_id": "018f5b7e-7540-7000-8000-000000000001",
  "data": {
    "artifact_names": ["source.json", "analysis.json", "note-plan.json"],
    "required_work": ["inspect_source", "write_analysis", "write_note_plan"]
  },
  "warnings": [],
  "error": null
}
```

The example run ID is synthetic. Actual output also supplies validated absolute artifact paths. Standard output contains exactly one JSON envelope; progress goes to standard error as compact events. Large source content is written to files, not dumped into the envelope.

Envelope statuses are `ok`, `awaiting_agent`, `needs_input`, `blocked`, `error`, and `uncertain`. Detailed run states remain in `data.run_status`. A model handoff is a successful protocol exchange but uses exit code 10; host adapters must not report it as a crash or retry it automatically.

Exit codes: `0` completed/no-op; `10` actionable handoff or user input; `20` invalid input/configuration; `30` environment/authentication/permission block; `40` resource drift or conflict; `50` transient remote failure; `60` uncertain remote mutation; `70` unsupported schema/capability. Error codes provide specificity, such as `ENV_CREDENTIAL_STORE_UNAVAILABLE`, `AUTH_LOGIN_REQUIRED`, `SCOPE_MISSING`, `RESOURCE_FORBIDDEN`, `SCHEMA_DRIFT`, and `REMOTE_RESULT_UNCERTAIN`.

The Paper2Lark envelope is separate from lark-cli's envelope. The adapter checks `ok`, process exit, warnings, partial results, and ignored fields according to command-specific contracts; it does not look for a success `code == 0` field.

## 12. Lark adapter and environment diagnosis

Only `lark/` may construct lark-cli operations. It exposes typed functions for resolving Wiki/Base URLs, fetching fields/records/documents, creating resources, updating selected fields, extending keyword options, and patching documents. Workflow code never assembles raw API requests.

Execute an argument vector with `shell=False` where supported. Windows npm wrapper resolution must be tested; if a wrapper needs a shell, stage all JSON/XML in private files and use a narrowly tested fixed launcher. Never interpolate paper titles, model output, file contents, tokens, or error hints into a shell command. Resolve the executable once per run and record its version.

Each run has a private working directory; CLI file parameters are relative to that directory, complying with its path restrictions. UTF-8 is explicit. Preserve distinctions between URL-style Markdown cell values, read-only server fields, and writable values. Follow pagination to completion for identity and vocabulary operations; a user-selected filtered view cannot hide duplicates.

### 12.1 Diagnostic order

1. Check executable and Python availability, command capabilities, runtime compatibility, and writable local state.
2. Inspect the configured account in the actual execution environment.
3. If credential access or networking is blocked by the host sandbox, return an environment-specific diagnostic and use the host's normal permission mechanism. Do not interpret it as revoked user authorization.
4. Let lark-cli refresh an existing session through its supported path.
5. Only if credentials are truly absent/expired after accessible verification, initiate the CLI login flow for the scopes required by the requested operation.
6. Present its authorization URL unchanged and its QR code; complete the device flow after the user reports completion. Never publish device codes in ordinary logs.
7. Distinguish missing scopes from missing resource access. Never switch to bot identity as an error workaround.

The credentials provider, not Paper2Lark, stores refresh/access tokens. No automatic `auth logout`, global permission expansion, lark-cli update, or secret copying. CLI-provided suggestions are parsed as data, not executed as arbitrary commands.

### 12.2 Retry policy

Read-only transient failures use a bounded retry policy: up to three total attempts, exponential backoff with jitter, and a server retry hint when provided. A retry wait beyond the current command's time budget returns a resumable state. Poll eventual consistency with bounded delays and a 30-second default deadline; pending verification remains resumable instead of reporting success.

Do not retry permission errors, invalid tokens/IDs, deleted resources, schema mismatches, or validation errors with unchanged parameters. For writes, first determine whether the operation is safely repeatable by comparing current state. Create/append operations with ambiguous outcomes enter reconciliation. A status setter can be retried only after reading the target and verifying its preconditions.

## 13. Setup, migration, and existing-library adoption

### 13.1 Bind existing resources

Inspect the Wiki, actual notes parent, Base/table, complete field definitions, status options, template, and a bounded sample of existing records. Produce a binding report describing supported workflows, missing requirements, and proposed role/field mappings. Binding itself does not rewrite old notes or reseed keywords.

`setup inspect` supplies observed resources and mechanically recognized candidates. The host completes ambiguous semantic mappings and includes the validated RoleMap in `BindingPlan` before `setup apply`. The runtime does not pretend it can understand arbitrary field labels or custom prose without that host interpretation. Deduplication later uses the complete relevant record scope, not the bounded setup sample.

The current personal library already has the 12-field schema and 45 English options. Its existing note is a legacy note without a Paper2Lark publication baseline, so it starts in append-only update mode. The recent migration script under `.paper2lark-work/` is a private operational artifact, not production code to package.

A missing paper identifier on an existing record is not grounds for creating a new row. Match the source URL or inspect the linked note, then fill an identifier only when verified. Preserve curated titles, notes, priorities, statuses, and the system creation timestamp.

### 13.2 Create new resources

Setup first emits a concrete plan including language, names, initial fields, default status behavior, keyword seed, and template. Under the user's creation request:

1. Create a Wiki space and persist its ID.
2. Create the Base and first table with the complete initial field schema; persist IDs.
3. Mount/link the Base in the Wiki using a capability-tested CLI path; do not equate a Drive folder token with a Wiki parent.
4. Create a notes parent, template, and home/navigation document with explicit locations.
5. Create useful status views using the same underlying table.
6. Resolve and verify the resulting resources, then save the binding.

Each operation is separately journaled. A half-completed setup resumes from known resources. It does not automatically delete the Wiki or Base as rollback. Missing permission to create a Wiki offers a bind-existing route without pretending provisioning succeeded.

### 13.3 Customization changes

Renaming fields/headings or moving pages inside the permitted library should not duplicate resources. If a template is replaced, recompile roles. If a bound resource moves across the configured scope or a field type becomes incompatible, require an explicit remapping. Setup never resets live defaults merely because the bundled assets changed in an upgrade.

## 14. Repository layout and module boundaries

```text
Paper2Lark/
  pyproject.toml
  README.md
  CHANGELOG.md
  skill_sources/{setup,add,read,library,doctor}.md
  host_adapters/{claude,codex}/
  src/paper2lark/
    cli.py
    contracts/{identity,source,analysis,template,publication,result}.py
    config/{load,bindings,migrations}.py
    state/{database,runs,operations,locks}.py
    lark/{runner,capabilities,errors,wiki,base,docs}.py
    papers/{identity,collect,query}.py
    keywords/{normalize,validate,reconcile}.py
    sources/{ingest,pdf,download,coverage}.py
    templates/{snapshot,compile,render,ownership}.py
    publishing/{plan,apply,verify,recover}.py
    workflows/{setup,add,read,library,doctor}.py
  assets/
    keywords.en.json
    templates/{en,zh-CN}/
    schemas/
  scripts/{build_plugins,validate_packages}.py
  tests/{unit,contract,integration,host}/
  docs/
    library-conventions.md
    installation/{claude,codex}.md
    configuration.md
    recovery.md
    compatibility.md
    superpowers/specs/2026-09-13-paper2lark-design.md
```

Dependencies flow from workflows to domain services and adapters. Contract dataclasses do not import lark-cli code. Renderers do not create remote resources. Recovery uses the same adapter and validation paths as normal publication. Package generation reads canonical source and never edits a user's installed plugin in place.

Core service contracts:

```python
inspect_library(request: SetupRequest) -> BindingPlan
apply_setup(plan: BindingPlan) -> LibraryBinding
collect_paper(request: AddRequest, binding: LibraryBinding) -> CollectionResult
prepare_read(request: ReadRequest, binding: LibraryBinding) -> ReadHandoff
validate_analysis(bundle: AnalysisBundle, source: SourceBundle) -> ValidationReport
compile_template(snapshot: TemplateSnapshot, interpretation: RoleMap) -> CompiledTemplate
plan_keywords(proposal: KeywordProposal, current: KeywordField) -> KeywordChangePlan
plan_publication(run_id: str, binding: LibraryBinding) -> PublicationPlan
apply_publication(plan: PublicationPlan) -> PublicationResult
reconcile_run(run_id: str) -> ResumeDecision
diagnose(profile: str) -> DiagnosticReport
```

All named types are versioned contracts with explicit ownership of target IDs and paths. `RoleMap` and `KeywordProposal` are model outputs validated against observed template/field data. `ResumeDecision` returns a typed action; it never executes a free-form model-generated command.

## 15. Implementation sequence and gates

| Milestone | Deliverable | Principal modules | Exit gate |
| --- | --- | --- | --- |
| M0: compatibility probes | Recorded host loading, launcher paths, CLI contracts, and publication primitives | host adapters, lark capabilities, package validation | Both hosts load a harmless skill and invoke the same pinned runtime; targeted Lark probes pass in disposable resources. |
| M1: configuration and diagnosis | Private cross-host home, state schema, bindings, account/environment diagnostics | config, state, lark runner/errors, doctor | Distinguish inaccessible credentials from missing login; no write or authorization reset during diagnosis. |
| M2: collection and vocabulary | Add/query/update with deduplication and keyword controls | papers, keywords, Base adapter, add/library skills | Repeated DOI/arXiv/URL inputs reuse records; counts and live vocabulary checks pass. |
| M3: source/model/template handoff | SourceBundle, fallback PDF extraction, analysis validation, current-template drafts | sources, contracts, templates, read skill | A paper is drafted in both hosts without optional skills; an abstract-only source is labeled correctly. |
| M4: publication and recovery | Verified Wiki notes, index updates, baselines, conflict-safe rereading | publishing, document adapter, operation journal | Fault injection at every write boundary produces recovery or an explicit uncertain state, not duplicate publication. |
| M5: provisioning and customization | New library setup, migration plans, remapping, English/Chinese assets | setup workflow, Wiki/Base creation, template roles | Fresh and existing libraries both work; renamed fields and human-edited sections remain intact. |
| M6: public release | Two self-contained packages, installation docs, sanitized fixtures, release report | build scripts, host adapters, docs | Each advertised platform/host combination passes installation and end-to-end checks from the release archive. |

M0 must specifically test `docs +create --parent-token`, returned document/Wiki IDs, field full-definition update behavior, record URL normalization, document revision preconditions, source asset handling, and preservation of a copied template's supported resources. Failed probes narrow the supported capability or select the documented fallback; they do not justify guessing flags or bypassing validation.

**M0 implementation amendment (2026-09-13):** See [the compatibility report](../../compatibility-m0.md). The tested append API accepted a stale revision, so automatic in-place updates must remain disabled until an atomic primitive is verified; creating a separate note revision is the default fallback. Native copy is verified only for paragraphs and a PNG. The dependency-free M0 diagnostic uses the same pinned stdlib zipapp in both hosts; dependency wheels and persistent bootstrap remain later work.

**M1 implementation amendment (2026-09-14):** See [the M1 compatibility report](../../compatibility-m1.md). Version 0.2.0 provides a shared private home, config/state schema v1, existing-library bindings and read-only diagnosis. SQLite inspection refuses active WAL and rollback journals to avoid side effects or stale immutable reads. Binding commits use a process-shared lock and compare the pre-inspection value before replacement. Paper collection and all remote mutation remain disabled.

**M2 implementation amendment (2026-09-15):** See [the M2 compatibility report](../../compatibility-m2.md). Version 0.3.0 adds canonical DOI/arXiv/URL/exact-content identities, schema-v2 library-scoped identity state, a typed Base adapter, fill-only collection, read-only intersection queries and explicit status/priority/keyword updates. The Claude and Codex packages share one dependency-free zipapp and four small skills. Remote mutation is covered through a real subprocess boundary with a synthetic provider; existing-library acceptance passed with read-only list and preview operations and byte-identical private binding/state before and after. Paper reading, note publication/recovery and new-library provisioning remain M3-M5 work.

**M3 implementation amendment (2026-09-15):** See [the M3 compatibility report](../../compatibility-m3.md). Version 0.4.0 adds write-once private reading artifacts with hash verification, bounded text/abstract/PDF source bundles, explicit component coverage, current-template snapshots and ordered role maps, evidence-linked research/review analysis validation, and a local template-aware Markdown draft with provenance. Its handoff contains concrete run/paper identifiers, artifact paths, observed template blocks and host-written schemas. PDF page locators require verified `/Pages` order; abstract main-text coverage cannot be promoted. M3 supports heading, paragraph, list, table, callout and equation note blocks; figure/table asset ingestion and image-reference blocks are deferred. The Claude and Codex packages still share one dependency-free standard library zipapp; optional reader skills remain external. Draft-only acceptance passed through both generated launchers with zero remote writes, including an abstract-only coverage case. M3 does not publish Wiki notes or update index note fields; publication/recovery and provisioning remain M4-M5 work.

**M4 implementation amendment (2026-09-15):** See [the M4 compatibility report](../../compatibility-m4.md). Version 0.5.0 adds schema-v3 operation journals, verified publication baselines and durable active-run reservations, immutable `publish plan` artifacts with crash adoption, separate-note Wiki publication, full-content-digest readback/placement verification, `publish apply` reconciliation, read-only `runs resume` guidance, and explicit local `runs cancel` abandonment with artifact retention. A lost create response is never retried blindly: recovery compares the planned child snapshot, embedded run/record/source markers and complete content digest, then adopts exactly one verified candidate. The index is updated only after note verification and a final managed-field precondition read; note-link/summary drift blocks completion, while earlier user-changed status/keywords are preserved. Identical completed plans are no-ops. The Base batch API has no atomic compare-and-swap, so edits occurring after the final precondition read remain a documented service-level race. The core remains a dependency-free standard-library zipapp; library provisioning and customization remain M5.

**M5 implementation amendment (2026-09-16):** Version 0.6.0 adds immutable setup plans, a private atomic setup journal, a typed Wiki/Base provisioning adapter, English/Chinese schema/template assets, additive missing-field migration and explicit field/status remapping. New Bases are created as Wiki nodes, avoiding asynchronous Drive moves. Setup never converts existing columns, replaces live vocabulary, edits templates or deletes created resources. Known IDs survive partial failure; an uncertain create requires an explicitly supplied resource reference verified against the plan, never blind recreation. An exact already-committed binding completes its journal after a crash without revalidating later human customizations. The setup journal is separate from schema-v3 reading state and persists under the shared home. Both generated packages remain dependency-free and below 256 KiB. See [M5 evidence and live-testing limits](../../compatibility-m5.md).

M2 can ship a private alpha that manages an existing library. M4 is the first complete personal reading workflow. M6 is the public-release gate; creating new libraries is not postponed beyond it.

No production library migration occurs as a side effect of tests. Existing-library integration testing is read-only unless the user explicitly selects test records/resources. Creation and fault-injection tests run in a disposable namespace, and cleanup is explicit and scoped.

## 16. Acceptance and test strategy

### 16.1 Deterministic and adapter tests

| ID | Scenario | Required outcome |
| --- | --- | --- |
| T01 | DOI URL case/encoding variants | Same normalized identifier; original source preserved. |
| T02 | arXiv abstract/PDF/versioned URLs | Same base identity; actual read version retained separately. |
| T03 | Identical title, different identifiers | No automatic merge. |
| T04 | Same file and revised PDF | Exact byte duplicate detected; revision not merged from title alone. |
| T05 | Keyword is four words or nine labels are selected | Validation fails before any remote mutation. |
| T06 | KD/LLM/RAG or case variants with matching live options | Existing canonical labels are used; no redundant option. |
| T07 | User adds a keyword after the initial snapshot | Refresh reconciles against it and preserves all live options. |
| T08 | Field renamed with ID unchanged | Write uses the correct field and preserves data. |
| T09 | Field deleted/type changed | Affected write stops with an exact mapping/type diagnostic. |
| T10 | Successful CLI envelope has no numeric code | Success is correctly parsed; no duplicate retry. |
| T11 | URL cell reads back as Markdown | Semantic URL comparison succeeds. |
| T12 | Partial success, ignored fields, or missing pages | Completion is withheld until the relevant verification passes. |
| T13 | Shell metacharacters/non-ASCII text in a paper title | Passed as data without shell execution or corruption. |
| T14 | CLI network/credential-store sandbox failure | Environment error; no needless login/logout. |
| T15 | Credentials exist but need refresh | CLI refresh path used without a new device flow when successful. |
| T16 | Different account becomes active | Mutation stops with `ACCOUNT_MISMATCH`. |

### 16.2 Workflow and fault-injection tests

| ID | Scenario | Required outcome |
| --- | --- | --- |
| T17 | No optional reading skills | Built-in extraction/handoff/draft path works for a text PDF. |
| T18 | Only abstract is available | No full-read claim; coverage and missing-source outcome are explicit. |
| T19 | Review paper | Taxonomy/evidence synthesis replaces inappropriate single-method demands. |
| T20 | User edits a protected or formerly AI-written section | Original remote content remains intact. |
| T21 | Legacy note has no baseline | Append-only update; no whole-document replacement. |
| T22 | Template changes between draft and publish | Stale template is detected and regeneration or explicit revision choice occurs. |
| T23 | Create commits, then its response is lost | Reconcile a verified candidate or return uncertain; never blind recreate. |
| T24 | Note verifies, then index update fails | Resume updates the same record and retains the same document. |
| T25 | Append commits, then process crashes | Read-back matching prevents duplicate append. |
| T26 | User changes priority/status during reading | Unrelated/user changes survive publication. |
| T27 | Two hosts target the same paper | One active run; the other can resume or wait. |
| T28 | Plugin upgrades while a run is incomplete | Existing artifacts remain readable; runtime/schema compatibility is checked. |
| T29 | State is lost or another machine binds the library | Remote records are rediscovered; note updates remain conservative. |
| T30 | Unsupported resource in a template | Resource preserved via tested path or clear supported-operation block, never silently dropped. |
| T31 | Setup fails after creating Wiki/Base | Resume uses persisted resources; no duplicate or destructive rollback. |
| T32 | User asks for a local draft without saving | No Base record, keyword option, reading-status, or Wiki mutation. |
| T33 | Text is unchanged but a human comment was added | Comment-bearing blocks remain intact; update uses a safe alternative. |
| T34 | Resume a completed run or publish an identical plan | Verified no-op; no duplicate section or timestamp churn. |

### 16.3 Host and release evaluation

Run the same natural-language scenarios in Claude Code and Codex: create/bind, add a paper, collect without reading, read-and-save, draft without saving, list pending papers, update priority, and resume a failed publication. Check tool traces and remote outcomes, not just the final wording.

Test the actual generated packages from paths containing spaces and non-ASCII characters, launched from unrelated working directories. Confirm both hosts locate the correct runtime and the same state root. Validate installation and update behavior in fresh sessions. Record exact host/CLI/Python/runtime versions in `docs/compatibility.md`.

Target Windows and macOS for initial host validation; run core/adapter contract CI on Windows, macOS, and Linux. Advertise only combinations that actually passed host testing. An unavailable test environment is reported as unverified, not counted as a pass. A Linux host release can be enabled after its own smoke test without changing the core architecture.

Quality evaluation checks evidence fidelity, accurate coverage, template adherence, English keyword reuse, human-area preservation, and absence of unrequested writes. Structural validation cannot prove scientific correctness; numerical and interpretive claims need source-based model review and representative human spot checks.

## 17. Privacy, input boundaries, and operational constraints

Source papers and downloaded content are data. Templates control note structure and writing instructions within the user's request; they cannot redirect the configured library, request credential disclosure, or authorize unrelated actions. Resource targets come from validated bindings and explicit user overrides.

The runtime does not execute paper code, TeX commands, arbitrary URLs embedded as scripts, or CLI suggestions returned as text. Downloaded assets are bounded in size/time, validated for type, and stored under the run directory; redirects and archive paths are checked before use. Private Lark resources go through lark-cli rather than a generic downloader carrying credentials.

Record only the error and operation details needed for recovery. Redact tokens, authorization/device codes, and secrets before persisting diagnostics. Source text and complete notes are not copied into general logs. Optional external extraction tools may have their own network behavior; select them transparently and do not upload local papers to an unrelated service by default.

Host permission policies remain effective. Paper2Lark can explain the required operation and let the host request its normal execution permission; it cannot automatically escape a sandbox or promise never to require a permission prompt.

## 18. Release checklist and deferred extensions

The first public release requires:

- Both generated plugin packages validated and installed from release artifacts.
- Recorded capability checks for the supported lark-cli version(s).
- Bind-existing and create-new workflows complete.
- No optional reading skill required for the baseline text-PDF path.
- All deterministic constraints, conflict tests, and uncertain-write tests passing.
- Human content and current personal library conventions preserved.
- English project documentation and English/Chinese initialization templates.
- No personal URLs, account identifiers, credentials, paper caches, authorization QR images, or runtime journals in release archives.
- Included dependency licenses/notices and a repository license selected by the owner before publication; optional third-party skills are not bundled.

Later additions can build on existing contracts: topic synthesis reads verified notes and source references; research Q&A distinguishes paper evidence from AI judgments; batch reading schedules the same durable runs; an optional MCP facade calls the same typed services. None requires replacing the paper index or publishing layer.

## 19. Evidence and limitations of this design

The existing library migration established practical examples of field IDs, URL-style cell normalization, DocxXML edits, keyword option changes, and user-identity access through the local CLI. Those observations inform the adapter tests; they do not substitute for testing fresh library creation, cross-host packaging, or failure recovery.

Platform sources were checked on 2026-09-13. Manifest/marketplace capabilities must be revalidated at release. Public documentation does not establish that this plugin will run on every surface carrying the Claude or Codex name. The compatibility matrix, generated artifacts, and integration gates define the actual supported product.

The repository currently contains conventions, a keyword seed, and private migration artifacts. This document authorizes no additional Lark changes, installs, marketplace publication, or project implementation by itself; it specifies the proposed build once the user proceeds.
