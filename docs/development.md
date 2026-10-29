# Development and validation

## Local environment

Use Python **3.11+**, as declared in [pyproject.toml](../pyproject.toml).

Linux:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

Training dependencies are optional in `pyproject.toml`. Check Python, PyTorch/CUDA,
and workbench compatibility before installation. The requirements files do not
pin all transitive versions and are not lockfiles.

## Tests

```bash
python -m pytest --collect-only
python -m pytest tests/test_runtime_config.py
# Linux with Bash and required dependencies:
bash tests/run_tests.sh
# Prepared container deployment:
docker exec openclaw bash /home/tenazas/tests/run_tests.sh
```

Pytest configuration adds the repository, workspace, and runtime to its import
path. The Bash runner sets its own `PYTHONPATH` and executes curated suites; it
does not automatically discover every test file.

`integration` and `live` markers are declared, but do not assume every test with
infrastructure dependencies has a marker. Inspect the selected suite first.
Collection can also trigger import-time effects. Use an isolated environment
without personal credentials or device access for initial inspection.

For documentation changes, check paths, links, fenced examples, diagrams, and
consistency with source. Running real workflows, bots, or financial jobs is not
required to validate Markdown. Derive test totals from actual runs.

## Style and contracts

```bash
python -m ruff check workspace/runtime tools tests
```

Ruff settings live in `pyproject.toml`. Separate existing lint debt from regressions
introduced by your change.

Read the [routing contract](../workspace/runtime/INTERFACE_CONTRACT.md) before
editing routing. Keep canonical labels in `routes_contract.py`; review classification,
dispatch, metrics, dashboards, and relevant tests together.

## Configuration and review

- Read [CLAUDE.md](../CLAUDE.md) and applicable repository instructions.
- GSD prompts live in `.codex/prompts/`; their engine must be installed separately.
  This checkout does not include `.claude/get-shit-done`.
- Separate gateway schema configuration from Tenazas extensions.
- Use placeholders in examples. Inspect diffs for credentials, personal data,
  CVs, logs, and generated operational artifacts.
- Explain behavior changes, validation, and any NAS-only checks in the PR.

## Documentation maintenance

Keep maintained guides in English and use relative links that work on GitHub.
Preserve literal identifiers, runtime log strings, and supported user command
examples when their spelling matters. Historical milestone plans remain records
of their original decisions and results.

Update diagrams and guides when services, mounts, flags, ports, or APIs change.
Mermaid diagrams belong in Markdown so their source and rendered GitHub view stay
reviewable together. Record the date and scope of operational reviews.
