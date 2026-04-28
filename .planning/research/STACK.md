# Technology Stack — Refactoring Tools

**Project:** Tenazas Runtime Refactor
**Researched:** 2026-04-28

## Recommended Stack

### Atomic File I/O (stdlib)

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| `tempfile.NamedTemporaryFile` | stdlib | Create temp file in same directory | Same-filesystem rename guarantees atomicity on Linux |
| `os.replace()` | stdlib (3.3+) | Atomic rename over existing file | Cross-platform atomic replace; works on ext4/btrfs (NAS) |
| `fcntl.flock()` | stdlib (Linux) | Cross-process advisory file locking | Only option for file locking without new deps; Linux-only but container is Linux |
| `pathlib.Path` | stdlib (3.4+) | File path manipulation | Already used in runtime_config.py; cleaner than os.path |

**Pattern — `atomic_io.py` (new shared module):**
```python
import fcntl
import json
import os
import tempfile
from pathlib import Path

def write_json_atomic(path: Path, data: dict) -> None:
    """Write JSON atomically: temp file + fsync + rename."""
    path = Path(path)
    dir_ = path.parent
    dir_.mkdir(parents=True, exist_ok=True)
    fd = tempfile.NamedTemporaryFile(
        mode="w", suffix=".tmp", dir=dir_, delete=False
    )
    try:
        json.dump(data, fd, indent=2, ensure_ascii=False)
        fd.write("\n")
        fd.flush()
        os.fsync(fd.fileno())
        fd.close()
        os.replace(fd.name, str(path))
    except BaseException:
        fd.close()
        try:
            os.unlink(fd.name)
        except OSError:
            pass
        raise

def read_json_safe(path: Path, default=None):
    """Read JSON with fallback on corruption."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default if default is not None else {}

class FileLock:
    """Cross-process advisory file lock using fcntl.flock()."""
    def __init__(self, path, timeout=5):
        self.lock_path = str(path) + ".lock"
        self.timeout = timeout
        self._fd = None

    def __enter__(self):
        self._fd = open(self.lock_path, "w")
        fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *args):
        if self._fd:
            fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
            self._fd.close()
            self._fd = None
```

**Confidence:** HIGH — `os.replace()` is atomic on POSIX when source and dest are on the same filesystem. The NAS uses btrfs/ext4. `fcntl.flock()` is advisory but sufficient when all writers cooperate (which we control).

### Configuration Centralization (stdlib)

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| `dataclasses` | stdlib (3.7+) | Typed config structure with validation | Zero deps, `__post_init__` for validation, immutable with `frozen=True` |
| `os.environ` | stdlib | Environment variable overrides | Already used inconsistently; standardize the pattern |
| `json` | stdlib | Load openclaw.json | Already the config format; no reason to change |

**Pattern — expand `runtime_config.py`:**
```python
from dataclasses import dataclass, field
import os
import json
from pathlib import Path

@dataclass(frozen=True)
class InfraConfig:
    ollama_gpu_url: str = "http://192.168.2.6:11434"
    ollama_nas_url: str = "http://127.0.0.1:11434"
    chromadb_url: str = "http://192.168.2.2:8100"
    hue_bridge_ip: str = "192.168.2.11"
    deco_ip: str = "192.168.2.30"
    nas_ip: str = "192.168.2.2"
    homelab_ip: str = "192.168.2.6"
    vpn_proxy: str = "http://192.168.2.2:8888"

    def __post_init__(self):
        # Validate URLs are well-formed
        for name, val in self.__dict__.items():
            if "url" in name and not val.startswith("http"):
                raise ValueError(f"{name} must be an HTTP URL, got: {val}")

def load_infra_config() -> InfraConfig:
    """Load from openclaw.json with env var overrides."""
    overrides = {}
    for fld in InfraConfig.__dataclass_fields__:
        env_key = f"TENAZAS_{fld.upper()}"
        env_val = os.environ.get(env_key, "").strip()
        if env_val:
            overrides[fld] = env_val
    return InfraConfig(**overrides)
```

**Why dataclass over dict:** Type safety, IDE autocomplete, validation in `__post_init__`, immutable with `frozen=True` (prevents accidental mutation). Still pure stdlib.

**Why NOT pydantic:** Adds a dependency. Dataclasses are sufficient for flat config with simple validation. Pydantic is overkill for 15 string fields.

**Confidence:** HIGH — dataclass config is a well-established Python pattern. The existing `runtime_config.py` already uses this approach partially.

### Static Analysis (dev-only, NOT in container)

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| Ruff | 0.8+ | Linting + formatting | 10-100x faster than pylint; replaces flake8+isort+pycodestyle; written in Rust |
| ast (stdlib) | stdlib | Import dependency mapping | Zero deps; 30-line script extracts all imports from 158 modules |

**Ruff configuration (`ruff.toml` at repo root):**
```toml
[lint]
select = [
    "E",    # pycodestyle errors
    "F",    # pyflakes
    "I",    # isort
    "UP",   # pyupgrade (modernize syntax)
    "B",    # bugbear (common bugs)
    "SIM",  # simplify
    "RUF",  # ruff-specific
]
ignore = [
    "E501",  # line length (existing code is inconsistent)
]
target-version = "py311"

[lint.per-file-ignores]
"tests/*" = ["B", "SIM"]
```

**Why Ruff over pylint:** Pylint is thorough but painfully slow on 158 files. Ruff runs the full suite in <1 second. For a refactor where you need rapid feedback loops, speed matters. Ruff catches the same import/unused/style issues that matter for refactoring.

**Why NOT mypy (yet):** The codebase has no type annotations beyond a few `dict | None` hints. Adding mypy enforcement to 158 untyped modules would generate thousands of errors and block progress. Revisit after refactor when interfaces are stable.

**Confidence:** HIGH — Ruff is the de facto standard Python linter as of 2025. Widely adopted, actively maintained by Astral.

### Import Dependency Mapping (stdlib)

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| `ast` module | stdlib | Parse imports from .py files | Zero deps, reliable, no execution needed |
| `ast.parse()` + `ast.walk()` | stdlib | Extract Import/ImportFrom nodes | Standard approach for static import analysis |

**Pattern — `scripts/import_graph.py` (dev tool):**
```python
import ast
import sys
from pathlib import Path

def get_imports(filepath: Path) -> set[str]:
    tree = ast.parse(filepath.read_text())
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])
    return imports

# Usage: python import_graph.py workspace/runtime/
# Outputs adjacency list of module dependencies
```

**Why NOT pydeps:** pydeps requires graphviz and dot installed, generates images. For a refactor, a text adjacency list is more actionable. If visualization is later desired, pydeps can be added as a dev-only tool.

**Confidence:** HIGH — `ast.parse()` is the standard way to statically analyze Python imports. Used by ruff, pylint, and every import-analysis tool internally.

### Error Handling Standardization (stdlib)

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| `logging` | stdlib | Structured JSON logging | Already used but inconsistently; standardize format |
| `contextlib` | stdlib | Context managers for cleanup | `contextlib.suppress()` for clean error ignoring |

**Pattern — standardized logger setup:**
```python
import logging
import json

class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "ts": self.formatTime(record),
            "level": record.levelname,
            "module": record.module,
            "msg": record.getMessage(),
        })

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.FileHandler(f"/home/tenazas/runtime/logs/{name}.log")
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
```

**Confidence:** HIGH — JSON structured logging is already the stated convention; this just standardizes it.

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| File locking | `fcntl.flock()` | `filelock` PyPI package | Adds dependency; fcntl is sufficient for Linux-only |
| File locking | `fcntl.flock()` | `sqlite3` for state | Out of scope per PROJECT.md; atomic JSON is sufficient |
| Config | `dataclasses` | `pydantic` | Adds dependency; overkill for flat string config |
| Config | `dataclasses` | `configparser` (INI) | Would require migrating from JSON format |
| Linting | Ruff | pylint | 100x slower; Ruff covers refactoring-relevant rules |
| Linting | Ruff | flake8+isort+black | Ruff replaces all three in one tool |
| Import analysis | `ast` stdlib | `pydeps` | Requires graphviz; text output is more actionable |
| Type checking | Skip (for now) | mypy | 158 untyped modules = thousands of errors; defer |

## Installation

### Dev machine (Windows, for linting before push)
```bash
pip install ruff
# Or via pipx:
pipx install ruff
```

### Container (NO changes needed)
All runtime tools are Python stdlib. No new pip packages.

### Ruff pre-commit (optional, dev-only)
```bash
pip install pre-commit
# .pre-commit-config.yaml:
# - repo: https://github.com/astral-sh/ruff-pre-commit
#   rev: v0.8.0
#   hooks:
#     - id: ruff
```

## Sources

- [Python os.replace() documentation](https://docs.python.org/3/library/os.html#os.replace)
- [Python fcntl module documentation](https://docs.python.org/3/library/fcntl.html)
- [Python dataclasses documentation](https://docs.python.org/3/library/dataclasses.html)
- [Ruff - Astral](https://astral.sh/ruff)
- [Atomic file writes discussion on Python.org](https://discuss.python.org/t/adding-atomicwrite-in-stdlib/11899)
- [Python Configuration with dataclasses](https://medium.com/@RampantLions/python-configuration-good-practices-argparse-configparser-os-environ-and-dataclasses-373490f33116)
- [pydeps - Python module dependency graphs](https://github.com/thebjorn/pydeps)
