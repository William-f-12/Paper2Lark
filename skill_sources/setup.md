# Set Up a Paper Library

Use this workflow when the user requests a new Paper2Lark library or an additive migration of an existing bound index.

Resolve this installed `SKILL.md` and its `../../scripts/paper2lark.py` launcher to absolute paths. Use Python 3.11+. Put global options before `setup`; use the user's actual private home and profile consistently:

```text
python LAUNCHER --home ABSOLUTE_HOME --profile PROFILE setup plan --input REQUEST
python LAUNCHER --home ABSOLUTE_HOME --profile PROFILE setup apply --plan SAVED_PLAN
python LAUNCHER --home ABSOLUTE_HOME --profile PROFILE setup show --id UUID
```

Require one JSON stdout document. Continue only on `ok: true`; inspect stable error codes otherwise. Do not expose credentials or reset authentication. Run local `config init` or `state init` only as part of explicitly requested initial setup; preserve existing configuration.

## Choose the operation

For existing Wiki, Base/table and template resources, retain the existing `bind` workflow. Migration requires that binding and adds missing fields without converting columns, replacing options or overwriting the template. Preserve renamed fields by ID; use explicit compatible remappings where necessary.

For a new library, obtain the actual HTTPS site origin and chosen name. Save private request JSON outside the plugin:

```json
{"schema_version":1,"mode":"create","site_url":"https://example.feishu.cn","name":"Paper Library"}
```

Replace the example origin and name with the user's choices. An already bound profile needs a separate chosen profile/home for creation.

For migration:

```json
{"schema_version":1,"mode":"migrate"}
```

Optional `field_map` maps logical field keys to observed field IDs; `status_map` maps `unread`, `reading`, or `read` to existing labels. Do not guess IDs or labels.

Library assets use `en` or `zh-CN` from saved configuration or the global `--library-language` override. Note language is separate. Resource environment overrides cannot select setup targets.

## Plan and apply

Planning creates local recovery artifacts and performs read calls; it makes no remote writes. Inspect the returned `plan_path`: account, profile, destination, language, fields, mappings and actions. Apply that exact saved plan when the user's request authorizes its changes. Preserve saved artifacts for recovery. Successful setup binds only verified resources.

## Recover or abandon

After interruption or uncertain results, run `setup show --id UUID` and inspect `pending_step` and operation evidence. Never blindly retry an uncertain creation. Confirm the exact existing resource and its parent/type before preparing adoption JSON:

```json
{"step":"root","reference":{"node_token":"VERIFIED_NODE_TOKEN","obj_token":"VERIFIED_OBJECT_TOKEN","space_id":"VERIFIED_SPACE_ID"}}
```

Reference keys depend on operation kind: space uses `space_id`; node uses all three keys above; table uses `table_id`; field uses `field_id`; template uses `document_id`. Use the reported step and confirmed reference object, then run `setup apply --plan SAVED_PLAN --adopt ADOPTION_JSON`. If identity cannot be confirmed, leave the operation blocked and report what evidence is missing.

Only an explicit abandonment request authorizes `setup cancel --id UUID`. Cancellation retains local artifacts and existing remote resources; it does not roll back creation. Report status, verified resource references and any remaining recovery step.
