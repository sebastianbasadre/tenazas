# OpenClaw configuration schema notes

Observation date: **2026-05-21**. Documentation reviewed **2026-10-28**.
These are historical findings; validate against the installed gateway version.

The current checkout contains `openclaw.example.json`, not a tracked root
`openclaw.json`. That helper template includes fields the gateway may reject.
See [Setup](../docs/setup.md).

## Strict gateway schema

The observed gateway rejected arbitrary root keys, including a custom `tenazas`
namespace, with `Invalid input` at `<root>`. Use separate files for Tenazas
extensions where supported by the relevant reader:

| File under `/home/tenazas/.openclaw/` | Historical use |
|---|---|
| `tenazas.json` | Tenazas-specific runtime flags |
| `tenazas_config.json` | Device configuration |
| `iol_config.json` | Broker tokens and runtime state |

The observed deployment's root keys were `auth`, `agents`, `models`, `tools`,
`commands`, `channels`, `gateway`, `mcp`, `plugins`, and `meta`. The smaller
root configuration inspected at that time used `auth`, `agents`, `models`,
`tools`, `channels`, and `gateway`.

## Observed nested keys

| Root | Observed fields or purpose |
|---|---|
| `auth` | `profiles`: provider authentication profiles |
| `agents` | `defaults`: model, workspace, timezone, compaction, concurrency, subagents |
| `models` | `providers`: gateway provider/model definitions |
| `tools` | `profile`, `web`, `exec`, `media` |
| `commands` | `native`, `nativeSkills`, `ownerDisplay`, `restart` |
| `channels` | `telegram`: token, allowlist, channel options |
| `gateway` | `auth`, `bind`, `controlUi`, `mode` |
| `mcp` | `servers` |
| `plugins` | `entries` |
| `meta` | `lastTouchedAt`, `lastTouchedVersion` |

This list records observations rather than a complete versioned schema. No valid
arbitrary-root extension namespace was identified in that review.

## Fine-tuned router flag

The inspected runtime classifier read a separate file:

```python
_tcfg_path = Path("/home/tenazas/.openclaw/tenazas.json")
# ...
if _tcfg.get("use_finetuned_router"):
    ...
```

Its configuration therefore belonged in `tenazas.json`:

```json
{
  "use_finetuned_router": true
}
```

Do not place that flag under a new gateway root namespace.

## Configuration helper

`tools/safe_config_edit.py` routes `tenazas.*` paths into `tenazas.json`:

```bash
python3 tools/safe_config_edit.py set tenazas.use_finetuned_router=true
```

Other paths target `openclaw.json` and are checked against known root keys before
writing. Those helper checks do not establish complete gateway schema compatibility.

## Recovery

Known backup conventions included `.last-good`, `.bak`, `.bak.1` through `.bak.4`,
and `.clobbered.*`. Verify the selected file before an operator recovery:

```bash
sudo docker exec openclaw cp /home/tenazas/.openclaw/openclaw.json.last-good \
  /home/tenazas/.openclaw/openclaw.json
sudo docker restart openclaw
```
