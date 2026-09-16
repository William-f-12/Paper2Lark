# M0 Compatibility Report

Tested on September 13, 2026 (America/Chicago; September 14 UTC).

## Environment and evidence

| Component | Observed version / result |
| --- | --- |
| OS | Windows |
| Python | 3.13.5 |
| Claude Code | 2.1.270 |
| Codex CLI | 0.154.0-alpha.6.2 |
| lark-cli | 1.0.89 |
| Runtime SHA-256 | `81ed4960ee32dfff54d17feb2db609dc79215225d0ba8132f2fbfc728b42620b` |

Local tests exercise real subprocesses from relocated packages and unrelated directories containing spaces and Chinese characters. Both hosts use the same pinned diagnostic archive. The native Codex installer accepted the local marketplace and installed the plugin in both an isolated configuration directory and the authenticated user's configuration. Claude used session-local `--plugin-dir` loading, rather than a persistent marketplace installation.

Both host traces include actual command output with `ok: true` and the runtime hash above, rather than only a model's success statement. Claude's initial sandboxed attempt stalled; the authenticated run outside that outer sandbox succeeded. Codex executed the installed skill's launcher in its read-only session. These tests reused existing credentials and did not initiate OAuth authorization.

Final local suite: 13 tests collected, 12 passed, one directory-symlink test skipped because this Windows account could not create a symbolic link. A separate native Windows junction probe passed: the builder rejected a redirected `dist` before writing and left its target empty. Both plugin manifests and the Claude marketplace passed validation. Independent code review found two defects (retained stray output files and malformed URL acceptance); failing regression tests reproduced them before the fixes, and those tests now pass.

Private raw command responses and host traces are retained under the ignored `.paper2lark-work/` directory. These contain account resource identifiers and must not be published. Public assertions below describe the synthetic fixtures without identifying the account.

## Lark findings

All writes used a newly created private test Wiki and a separate synthetic Base. The existing paper library was not modified. Resources are retained for inspection rather than automatically deleted.

| Probe | Observation | Implementation consequence |
| --- | --- | --- |
| Wiki parent creation | `docs +create --parent-token` created a child under the intended parent. | Supported on this tested version. |
| Returned identities | Create returned `data.document.document_id`, revision and a Docx URL; it did not return a Wiki node token. | Resolve with `wiki +node-get --node-token DOC_ID --obj-type docx`; verify parent and space before indexing. |
| Full field update | Existing option names/colors, `multiple`, description and default survived a complete definition PUT with one added option. | Read live definition, preserve writable properties, remove read-only `id`, add the option, write and read back. |
| URL readback | A raw URL in a URL-style text field came back as `[URL](URL)`. | Normalize a single URL/link before comparing; do not compare raw display strings. |
| Result envelope | Ordinary commands returned `ok: true`; record export returned an NDJSON manifest without that envelope. | Separate parsers; inspect nested result/warnings and exported pagination. |
| Revision precondition | After a revision-3 write advanced to revision 4, another append using revision 3 succeeded and advanced to 5. Readback contained both writes. | **Do not treat `--revision-id` as compare-and-swap.** |
| Image upload | A synthetic PNG was inserted with `docs +media-insert`. | Supported for the tested local PNG path. |
| Native Wiki copy | Copied text and image; the copied image received a new resource token. Downloaded bytes exactly matched the original. | Copy resources natively and re-resolve resource identities. Never assume tokens remain unchanged. |

Image fixture SHA-256: `e5f674719d78a0ae3ad32db0897d303652f20026845d745e47f8536cbf3330ce`.

The tested copy subset is paragraphs and a PNG with a caption. This does not establish support for equations, tables, attachments, whiteboards, embedded Base views or arbitrary custom template resources. Those capabilities remain disabled/unverified until their own probes pass. A one-pixel fixture proves byte preservation, not general image rendering quality.

## Required design adjustment

M0 characterizes the API; a failed capability is not a test to hide. The revision probe disproved the proposed atomic-precondition assumption for the tested append operation. Local locks and before/after reads cannot prevent concurrent human edits on Lark.

Consequently, future automatic publishing must default to creating a separate note revision when changing existing content. In-place replacement of human-editable notes remains disabled until a suitable atomic primitive is verified. A before/after conflict check may diagnose a race but must not be advertised as preventing it. Other update commands require independent characterization; this report does not generalize append behavior into a guarantee about every endpoint.

M0 deliberately uses a pinned standard-library zipapp instead of installing wheels: the probe has no external dependencies. Both host packages are self-contained, and the state path is outside plugin caches. Persistent environment/bootstrap behavior, configuration migrations and dependency wheels remain M1+ work.

## Reproduction

Run local tests and manifest validation from the repository root:

```powershell
python -m unittest discover -s tests -v
python scripts/build_plugins.py
claude plugin validate ./dist/claude/plugins/paper2lark
claude plugin validate ./dist/claude
```

Codex's installed plugin-creator validator can validate its manifest; the stronger integration check is installing the generated local marketplace with the commands in the README and invoking `paper2lark-probe` in a fresh session.

For live reproduction, use your own configured `lark-cli` and exclusively disposable resources:

1. Create a private test space using `wiki +space-create`, then a parent with `wiki +node-create`. Save every returned identity immediately. Do not repeat an uncertain create blindly.
2. Create a short Markdown document under that parent with `docs +create`. Resolve its Wiki identity using the documented arguments above and verify its parent.
3. Append using its returned revision. Append a different sentinel using that same, now stale revision. Fetch and check whether the second sentinel exists; record actual behavior, including the exact command.
4. Create a Base with a text title, multi-select keywords and URL-style source field. Read the keyword field; send its full writable definition plus one option using `base +field-update --yes`; read back and compare the old options and metadata.
5. Insert a synthetic record with a raw source URL. Read using `base +record-list --format ndjson --output ./records.ndjson`; inspect the file and `has_more` in the manifest.
6. Upload a known local PNG into the test document. Copy its Wiki node using `wiki +node-copy --yes` into the same test parent. Fetch the copy, download its image token and compare SHA-256 with the original.
7. Record resource IDs privately for later inspection or explicit cleanup. Never substitute a production paper library for the disposable space.

Read each installed command's `--help` and corresponding Lark skill reference before constructing payloads; CLI shapes vary by version. Authentication is owned by lark-cli. In this environment saved credentials worked outside the tool sandbox, so a sandbox `token_missing` response was not treated as evidence that a fresh OAuth login was necessary.

## Support boundary

This milestone proves a minimal local diagnostic, host packaging and the listed Lark primitives. It does not prove the later paper workflows, Windows Python versions other than 3.13.5, Linux/macOS, desktop UI installation, concurrent multi-machine operation, arbitrary templates or offline credential refresh.
