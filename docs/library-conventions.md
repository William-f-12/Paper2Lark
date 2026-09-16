# Paper library conventions

These conventions describe the current library workflow and requirements for the future Paper2Lark plugin. They are not an installed skill or an automatic validator.

## Language and customization

- Project documentation, code, and future skill instructions use English.
- Library labels and note prose may use a configured language; the current personal workflow uses Simplified Chinese.
- Keywords always use English, independently of the note language.
- Preserve original paper titles. Include the full title in each note even when the page uses a short title.
- Support creating a library or binding an existing library, index, and template.
- Resolve resources by stable IDs, with current field and status mappings. Respect user changes to names, structure, and templates. Never reset customization automatically.

## Index

The existing index retains its title, authors, year, venue, summary, keywords, priority, reading status, note link, and creation time. Add a source URL and a paper identifier. Venue covers conferences, journals, and preprint platforms.

Use `doi:<lowercase DOI>` or `arxiv:<ID without vN>` as a deduplication identifier. Record the version actually read in the note. Do not fabricate identifiers or merge papers solely because titles are similar. Verify the relationship between a preprint and a published article before consolidating their records.

## Controlled keywords

- Read the live keyword options before tagging each paper. The live index is authoritative; `assets/keywords.en.json` is only the initial seed.
- Match concepts semantically, including Chinese descriptions, abbreviations, spelling variants, and synonyms, to existing canonical labels.
- Each label contains at most three English words. Count hyphen-separated words separately; an acronym counts as one word.
- Use at most eight distinct labels per paper. Three to five relevant labels are usually sufficient. Do not add generic labels simply to reach a count.
- Reuse the canonical spelling. Do not create duplicates for case, number, abbreviation, or synonymous wording.
- Examples: KD maps to `Knowledge Distillation`, LLM maps to `Large Language Models`, and RAG maps to `Retrieval Augmented Generation`.
- Create a new label only when existing labels cannot accurately describe an important topic in the paper. Use a recognizable, reusable English term; never truncate a technical term mechanically.
- Re-read options before creating a new one, then add the option before assigning it to a record. Keep the index and note keywords identical.
- `Specialist Models` and `Mixture of Experts` are separate labels; do not automatically treat them as synonyms.

The live field description and template document these rules. They do not impose native Lark input limits. The future plugin must validate language, word count, tag count, duplicates, and membership before writing.

## Note template

Include the full title, authors, short title, year, venue, paper type, canonical identifier, actual source version, source URL, and canonical keywords.

Separate author claims, paper evidence, and AI analysis. Attach section, page, figure, or table locations to material claims and numbers. Assertions of priority or historical impact require checked external evidence; otherwise mark them unverified.

Preserve the section reserved for the user's questions and a separate human judgment section. Leave them unfilled during AI generation. Protect human edits elsewhere in a note too. If authorship or edit ownership is unclear, preserve the text and add a separate AI update rather than replacing it.

Record generation date, template revision, actual AI reading coverage, and missing material. Human reading and reproduction status are separate: AI analysis cannot establish that the user read or reproduced the work.

### Review variant

Retain bibliographic information, provenance, personal sections, and keyword rules. Adapt the main analysis to review scope, motivation, taxonomy, literature selection, consensus, disagreements, evidence quality, coverage bias, and research gaps. Record search methods, inclusion criteria, bias assessment, and heterogeneity when applicable and reported. Do not force a review into a single-method or original-experiment template.

### Quick overview

Use when requested. Include bibliographic information, a concise takeaway, research question, main idea or taxonomy, inspected evidence, limitations, and actual coverage. Mark unassessed content explicitly. An abstract-only overview is not full-text reading.

## Workflow and structure

The current personal workflow retains pending, reading, completed, and deferred statuses. It permits marking a paper completed after its note and index links are saved. This denotes workflow completion, not proof of personal reading or reproduction. Public configuration should make this behavior explicit.

Keep execution IDs, failure details, and intermediate artifacts in runtime storage rather than expanding the main index prematurely. On failure, resume from saved resources and finish missing writes instead of creating duplicates.

Keep the existing archive structure. New pages may use `YYYY · First author · Short title`. Use index views and keywords for multiple topic memberships. Related-paper links should include their relationship and a short reason. Add topic notes, method cards, and syntheses as needed; no retrieval service is required at this stage.
