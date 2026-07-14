# OpenClaw Config Schema Notes

Fecha: 2026-05-21

## Resumen

`openclaw.json` es validado por el gateway con schema estricto. No acepta claves
root arbitrarias: agregar `tenazas` u otra key fuera del contrato rompe el
arranque con errores tipo `Invalid input` en `<root>`.

Para extensiones propias de Tenazas, usar archivos separados dentro de
`/home/tenazas/.openclaw/`, especialmente:

- `tenazas.json`: flags/runtime config propios de Tenazas.
- `tenazas_config.json`: configuracion de dispositivos.
- `iol_config.json`: tokens/runtime state de IOL.

## Root keys observadas como validas

La configuracion valida actual en `state/openclaw.json` usa estas claves root:

- `auth`
- `agents`
- `models`
- `tools`
- `commands`
- `channels`
- `gateway`
- `mcp`
- `plugins`
- `meta`

El `openclaw.json` de root del repo es mas chico, pero conserva el mismo nucleo:

- `auth`
- `agents`
- `models`
- `tools`
- `channels`
- `gateway`

## Subkeys principales observadas

`auth`

- `profiles`: perfiles de proveedores de autenticacion.

`agents`

- `defaults`: modelo default, workspace, timezone, compaction, concurrencia y subagents.

`models`

- `providers`: proveedores/modelos disponibles para el gateway.

`tools`

- `profile`
- `web`
- `exec`
- `media`

`commands`

- `native`
- `nativeSkills`
- `ownerDisplay`
- `restart`

`channels`

- `telegram`: bot token, allowlist y opciones del canal.

`gateway`

- `auth`
- `bind`
- `controlUi`
- `mode`

`mcp`

- `servers`

`plugins`

- `entries`

`meta`

- `lastTouchedAt`
- `lastTouchedVersion`

## Claves arbitrarias / extensiones

No se encontro un namespace root valido para claves arbitrarias en
`openclaw.json`. La evidencia operacional previa confirma que agregar keys
desconocidas causa rechazo del gateway.

Decision: no extender `openclaw.json` para features propias de Tenazas.

## Finetuned router

El runtime ya implementa el flag en:

`/home/tenazas/runtime/classifier.py`

La lectura real es:

```python
_tcfg_path = Path("/home/tenazas/.openclaw/tenazas.json")
...
if _tcfg.get("use_finetuned_router"):
```

Por lo tanto, el lugar correcto para activar el router es:

`/home/tenazas/.openclaw/tenazas.json`

con:

```json
{
  "use_finetuned_router": true
}
```

No usar `openclaw.json` para este flag.

## safe_config_edit.py

`tools/safe_config_edit.py` aplica esta regla:

- `python3 safe_config_edit.py set tenazas.use_finetuned_router=true`
  escribe `use_finetuned_router=true` en `tenazas.json`.
- Paths sin prefijo `tenazas.` se tratan como cambios a `openclaw.json` y se
  validan contra las root keys conocidas antes de escribir.

## Rollback

Backups conocidos de `openclaw.json`:

- `/home/tenazas/.openclaw/openclaw.json.last-good`
- `/home/tenazas/.openclaw/openclaw.json.bak`
- `/home/tenazas/.openclaw/openclaw.json.bak.1` ... `.bak.4`
- `/home/tenazas/.openclaw/openclaw.json.clobbered.*`

Restauracion manual:

```bash
sudo docker exec openclaw cp /home/tenazas/.openclaw/openclaw.json.last-good \
  /home/tenazas/.openclaw/openclaw.json
sudo docker restart openclaw
```
