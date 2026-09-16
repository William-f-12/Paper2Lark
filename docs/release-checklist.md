# Paper2Lark release checklist

This is a release gate, not a certification record. Record actual results in the milestone compatibility report, with exact commit, package version, archive SHA-256, host versions, Python version, OS and dates. Leave unexecuted checks explicitly pending. A build, probe or synthetic-provider workflow cannot substitute for a real host-model end-to-end run.

## Package and provenance

- [ ] Run the full repository test suite and package build from a clean release checkout; record commands and results.
- [ ] Produce separate Claude and Codex ZIPs plus release checksums. Each ZIP contains a complete local marketplace: its host catalog, `plugins/paper2lark`, archive-root installation README and license.
- [ ] Confirm both packages contain the same pinned runtime, matching manifest/build versions, allowlisted standard-library-only payloads and no third-party reader requirement.
- [ ] Verify reproducible build/archive hashes and declared size limits. Inspect every archive entry for traversal, absolute paths, redirected files, unknown content and unexpected dependencies.
- [ ] Extract both ZIPs in unrelated paths with spaces and non-ASCII characters. Run their launchers from an unrelated working directory; ensure no source-checkout dependency.
- [ ] Scan archives, fixtures, docs and public evidence for credentials, private paths, resource/account IDs, bindings, databases, raw production responses and copyrighted paper content. Use synthetic public fixtures and sanitized reports.

## Installation and upgrade per advertised host/OS

- [ ] Use a clean isolated host configuration to register the extracted marketplace and install `paper2lark@paper2lark-local`. Start a new session and verify skill discovery and invocation through the actual host.
- [ ] Run package probe and offline doctor. Record native plugin-manager installation separately from session-only Claude `--plugin-dir` loading.
- [ ] Verify both hosts use the same explicit private home/profile and preserve configuration and durable state across package replacement.
- [ ] Exercise upgrade with unfinished setup/read artifacts. Verify setup's exact runtime-version guard, reading schema/hash validation, original-package recovery and artifact retention. Do not replace a live database blindly.
- [ ] Verify uninstall removes the intended host registration/cache while retaining private home and separately managed Lark authorization.
- [ ] Record platform-specific limitations. Do not advertise macOS, Linux, desktop or any other host/OS combination without its own installation and end-to-end evidence.

## Runtime coverage versus real end-to-end acceptance

- [ ] Record deterministic subprocess/provider tests for new/existing libraries, add/deduplicate/query/update, full/abstract drafts, publication, conflicts, retries and uncertain outcomes. Identify the provider as synthetic.
- [ ] In each advertised host/OS combination, install from the final release ZIP and issue natural-language requests through the actual model, with optional reader skills absent. Record skill selection, launcher execution and verified outputs.
- [ ] Use an authorized disposable live library to create English and Chinese libraries, bind an existing library and apply additive migration/remapping while preserving existing customizations.
- [ ] Through each host, collect the same paper twice, query it, draft from supported full text and abstract-only inputs, and verify honest coverage labels and English keyword limits.
- [ ] Through each host, read-and-save, verify Wiki placement/content and Base links/summary, then reread with protected human edits. Verify recovery/idempotency without duplicate publication.
- [ ] Exercise cross-host continuation using the same home/profile; record account/binding checks and preserved artifacts. Scope any live cleanup explicitly to disposable resources.
- [ ] Distinguish live remote checks from scripted runtime calls. Runtime-only tests do not establish natural-language model routing, actual paper comprehension or host permissions.

## Publish decision

- [ ] Review limitations against the design's M6 gate. If live host/model or platform evidence is missing, label the distribution a candidate with incomplete certification; do not mark M6 fully accepted.
- [ ] Check archive README instructions in the extracted layout and verify all documentation links. Source-root GitHub installation must not be advertised unless a built marketplace is actually published there.
- [ ] Attach only the verified final ZIPs/checksums and sanitized compatibility report to the matching version release. Preserve old release artifacts for runtime-pinned setup recovery.
- [ ] Review release text for accurate supported combinations, unmet checks and upgrade guidance. Obtain the user's publishing authorization if it has not already been given.
