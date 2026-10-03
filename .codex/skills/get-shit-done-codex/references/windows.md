# Windows + PowerShell conversion notes

## JSON parse

```powershell
$raw = & node .claude/get-shit-done/bin/gsd-tools.js state load --raw
$data = $raw | ConvertFrom-Json
```

## Key/value parse

```powershell
$raw = & node .claude/get-shit-done/bin/gsd-tools.js state load --raw
$out = @{}
$raw -split "`n" | ForEach-Object {
  if ($_ -match '^(.*?)=(.*)$') { $out[$Matches[1]] = $Matches[2] }
}
```

## Command invocation (important)

```powershell
# Good: path is separate from the command
$gsd = ".claude/get-shit-done/bin/gsd-tools.js"
$raw = & node $gsd init execute-phase 28 --raw
```

```powershell
# Bad: this fails because PowerShell treats it as one command token
$cmd = "node .claude/get-shit-done/bin/gsd-tools.js"
$raw = & $cmd init execute-phase 28 --raw
```

## Syntax notes
- Replace jq with `ConvertFrom-Json`.
- Avoid bash-only heredocs and command substitution.
- Use quoted paths for PowerShell compatibility.
