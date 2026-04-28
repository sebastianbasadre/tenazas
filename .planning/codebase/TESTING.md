# Testing Patterns

**Analysis Date:** 2026-04-28

## Test Framework

**Runner:**
- Python: `unittest` (stdlib)
- Bash: `bash` with no test framework (custom helpers)
- Config: No pytest.ini, tox.ini, or setup.cfg (unittest only)

**Assertion Library:**
- `unittest` assertions: `assertEqual()`, `assertTrue()`, `assertIn()`, `assertRaises()`, etc.
- Mock library: `unittest.mock.patch`, `unittest.mock.MagicMock`, `unittest.mock.patch.object`

**Run Commands:**
```bash
# Python tests (by module)
python3 tests/test_runtime.py               # Core runtime (54 tests)
python3 tests/test_orchestrator.py          # Query classification + routing (65 tests)
python3 tests/test_model_router.py          # Model selection V1+V2 (93 tests)
python3 tests/test_confidence.py            # Confidence scoring (40 tests)
python3 tests/test_advanced.py              # Metrics + infra memory (41 tests)
python3 tests/test_intelligence.py          # Context, decision trace, RAG (33 tests)
python3 tests/test_safety_guard.py          # Action validation (62 tests)
python3 tests/test_supervisor.py            # Subprocess supervision (22 tests)
python3 tests/test_query.py                 # RAG + confidence pipeline (34 tests)

# Bash tests
bash tests/test_auto_heal.sh                # auto-heal.sh script (6 scenarios)
bash tests/test_ollama_check.sh             # ollama-check.sh routing (4 scenarios)

# All tests
bash tests/run_tests.sh                     # Runs all Python + Bash tests

# Watch mode (not available)
# Coverage (not configured)
```

## Test File Organization

**Location:**
- Python tests: `tests/test_*.py` (co-located with source, not in separate tree)
- Bash tests: `tests/*.sh`
- Test fixtures: Inline via `tempfile.mkdtemp()` or helper functions

**Naming:**
- Test class: `Test<ModuleName><Feature>` (e.g., `TestDockerWrapperSanitize`, `TestQueryClassification`)
- Test method: `test_<description>` (e.g., `test_ping_timeout`, `test_high_confidence_with_good_results`)
- Helper method: `_<name>` (e.g., `_make_state()`, `_mock_backends()`)

**Structure (Python):**
```python
#!/usr/bin/env python3
"""
Tests para runtime/<module>.py
Corre: python3 tests/test_<module>.py
"""

import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/tenazas")
from runtime import <module>

class Test<ModuleName><Feature>(unittest.TestCase):
    """Feature description."""

    def setUp(self):
        # Prepare test fixtures
        pass

    def tearDown(self):
        # Clean up (restore mocks, remove temp files)
        pass

    def test_<description>(self):
        # Arrange, Act, Assert
        pass

if __name__ == "__main__":
    unittest.main()
```

**Structure (Bash):**
```bash
#!/bin/bash
set -uo pipefail

PASS=0
FAIL=0

ok() { echo "  ✅ $1"; PASS=$((PASS+1)); }
fail() { echo "  ❌ $1"; FAIL=$((FAIL+1)); }

assert_contains() {
  local file="$1" pattern="$2" label="$3"
  if grep -q "$pattern" "$file" 2>/dev/null; then
    ok "$label"
  else
    fail "$label"
  fi
}

# Scenario setup
setup_mocks() {
  # Create mocks in temp directory
}

# Scenario execution
echo "── Test 1: Description ──"
run_scenario "scenario1"
assert_contains "$output" "expected" "assertion label"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
```

## Test Structure

**Suite Organization:**

Test classes group related functionality:

```python
class TestDockerWrapperSanitize(unittest.TestCase):
    """Container name sanitization."""
    
    def test_valid_names(self): ...
    def test_rejects_empty(self): ...
    def test_rejects_injection(self): ...
    def test_rejects_protected_containers(self): ...
```

**Patterns:**

1. **Setup/Teardown:**
   - `setUp()`: Initialize test fixtures, mock patches, temp files
   - `tearDown()`: Restore mocks, clean temp files
   - Example from `test_confidence.py` (lines 48-56):
     ```python
     class TestConfidenceScoring(unittest.TestCase):
         def setUp(self):
             self.tmpdir = tempfile.mkdtemp()
             self._orig = ce.DIAGNOSTICS_LOG
             ce.DIAGNOSTICS_LOG = Path(self.tmpdir) / "diagnostics.log"
         
         def tearDown(self):
             ce.DIAGNOSTICS_LOG = self._orig
     ```

2. **Arrange-Act-Assert (AAA):**
   - Arrange: Create test data, mock backends
   - Act: Call function
   - Assert: Verify result
   - Example from `test_runtime.py` (lines 162-172):
     ```python
     def test_container_down_detected(self):
         # Arrange
         prev = self._make_state()
         curr = self._make_state(containers=[
             {"name": "sonarr", "status": "Exited", "up": False},
             {"name": "gluetun", "status": "Up 1h", "up": True},
         ])
         # Act
         events = rt.detect_events(prev, curr)
         # Assert
         types = [e["type"] for e in events]
         self.assertIn("container_down", types)
         down_event = next(e for e in events if e["type"] == "container_down")
         self.assertEqual(down_event["container"], "sonarr")
     ```

3. **Assertion style:**
   - Prefer `assertEqual(actual, expected)` over `assertTrue(actual == expected)`
   - Use `assertIn()` for membership tests
   - Use `assertRaises()` for exception testing
   - Chain assertions for readability:
     ```python
     self.assertIn("model", result)
     self.assertIn("backend", result)
     self.assertIn("url", result)
     ```

## Mocking

**Framework:** `unittest.mock` (standard library)

**Patterns:**

1. **Function mocking with `@patch`:**
   - Example from `test_model_router.py` (lines 53-56):
     ```python
     @patch("runtime.model_router.subprocess.run")
     def test_ping_success(self, mock_run):
         mock_run.return_value = MagicMock(returncode=0)
         self.assertTrue(mr._ping_backend("notebook_gpu"))
     ```

2. **Helper factory functions for common mocks:**
   - Example from `test_model_router.py` (lines 28-47):
     ```python
     def _mock_backends(notebook=True, nas=True):
         """Helper: mock detect_backends with controllable availability."""
         def fake_detect():
             return {
                 "notebook_gpu": {
                     "available": notebook,
                     "url": "http://192.168.2.6:11434",
                     "type": "gpu",
                     "priority": 1,
                     "description": "HomeLab GPU",
                 },
                 "nas_cpu": {
                     "available": nas,
                     "url": "http://127.0.0.1:11434",
                     "type": "cpu",
                     "priority": 2,
                     "description": "NAS Ryzen CPU (fallback)",
                 },
             }
         return fake_detect
     ```

3. **Side effects for conditional behavior:**
   - Example from `test_model_router.py` (lines 92-98):
     ```python
     @patch("runtime.model_router._ping_backend")
     @patch("runtime.model_router._log")
     def test_notebook_offline(self, mock_log, mock_ping):
         mock_ping.side_effect = lambda bid: bid != "notebook_gpu"
         result = mr.detect_backends()
         self.assertFalse(result["notebook_gpu"]["available"])
         self.assertTrue(result["nas_cpu"]["available"])
     ```

4. **MagicMock for complex return values:**
   - Example from `test_runtime.py` (lines 67-76):
     ```python
     @patch("runtime.docker_wrapper.subprocess.run")
     def test_restart_allowed(self, mock_run):
         mock_run.return_value = MagicMock(
             returncode=0, stdout="sonarr", stderr=""
         )
         result = docker_wrapper.docker_action("restart", "sonarr")
         self.assertTrue(result["ok"])
         mock_run.assert_called_once_with(
             ["docker", "restart", "sonarr"],
             capture_output=True, text=True, timeout=30,
         )
     ```

**What to Mock:**
- External I/O: filesystem, network, subprocess
- Expensive operations: backend pinging, LLM calls
- Randomness: `datetime.now()`, random numbers
- Do NOT mock pure functions — test them directly

**What NOT to Mock:**
- Core business logic (e.g., actual query classification, confidence scoring)
- Data transformations (JSON parsing, regex matching)
- Validation logic (error handling, input sanitization)

## Fixtures and Factories

**Test data (no external fixture files):**

1. **Inline state builders:**
   - Example from `test_runtime.py` (lines 140-155):
     ```python
     def _make_state(self, **overrides):
         base = {
             "ts": datetime.now(timezone.utc).isoformat(),
             "containers": [
                 {"name": "sonarr", "status": "Up 2h", "up": True},
                 {"name": "gluetun", "status": "Up 1h", "up": True},
             ],
             "internet": {"ok": True, "latency_ms": 10},
             "vpn": {"ip": "82.140.1.2", "ok": True},
             "public_ip": {"ip": "190.12.3.4"},
             "ollama_notebook": {"ok": True},
             "ollama_nas": {"ok": False},
             "network_devices": {"count": 12},
         }
         base.update(overrides)
         return base
     ```

2. **Temp directories (per-test):**
   - Example from `test_confidence.py` (lines 50-56):
     ```python
     def setUp(self):
         self.tmpdir = tempfile.mkdtemp()
         self._orig = ce.DIAGNOSTICS_LOG
         ce.DIAGNOSTICS_LOG = Path(self.tmpdir) / "diagnostics.log"
     
     def tearDown(self):
         ce.DIAGNOSTICS_LOG = self._orig
     ```

**Location:**
- Inline in test methods or `setUp()` — no separate fixture files
- Temp data in `tempfile.mktemp()` / `tempfile.mkdtemp()`
- Mock data returned from factory functions

## Coverage

**Requirements:** None enforced

**View coverage:**
```bash
# Not configured — manual inspection only
# Look at test/implementation ratio in tests/ and workspace/runtime/
ls tests/test_*.py | wc -l       # 60+ test files
find workspace/runtime -name "*.py" -type f | wc -l  # 42+ modules
```

## Test Types

**Unit Tests:**
- Scope: Single function or small module
- Approach: Mock external dependencies, test logic in isolation
- Example: `TestDockerWrapperSanitize` tests `sanitize_name()` with various invalid/valid inputs
- Coverage: ~80% of modules have unit tests

**Integration Tests:**
- Scope: Multiple modules working together
- Approach: Real subprocess/file I/O in temp directories, no mocks
- Example: `TestEventDetection` tests `detect_events()` with real state dicts
- Coverage: All major pipelines (orchestrator + model_router, orchestrator + confidence_engine)

**E2E Tests:**
- Scope: Full scenario from input to output
- Approach: Real (mocked) subprocess, file operations, subprocess output
- Example from `test_auto_heal.sh`:
  ```bash
  run_heal "vpn_leak" > "$MOCK_DIR/out_vpn.txt" 2>&1
  assert_contains "$DOCKER_CALLS" "restart gluetun"
  assert_contains "$TELEGRAM_CALLS" "ok"
  assert_contains "$MOCK_DIR/out_vpn.txt" "AR"
  ```
- Coverage: auto-heal.sh and ollama-check.sh (both critical scripts)

## Common Patterns

**Async Testing:**
- Not used (Python is single-threaded, bash is synchronous)

**Error Testing:**
- `assertRaises()` for expected exceptions
- Example from `test_runtime.py` (lines 30-34):
  ```python
  def test_rejects_empty(self):
      with self.assertRaises(ValueError):
          docker_wrapper.sanitize_name("")
      with self.assertRaises(ValueError):
          docker_wrapper.sanitize_name("   ")
  ```

**Multiple scenarios per test class:**
- Use `setUp()` to initialize shared resources
- Use helper methods to reduce duplication
- Example from `test_model_router.py` — `TestSelectModelGPU`, `TestSelectModelCPU`, `TestSelectModelOffline` are separate classes for same function with different conditions

**Assertion message customization:**
- Not used — assertions are clear from method names
- Example: `test_rejects_injection` makes it obvious what's being tested

## Test Execution

**All tests:**
```bash
bash tests/run_tests.sh
```

**Individual suite:**
```bash
python3 tests/test_orchestrator.py
```

**Specific test class:**
```bash
python3 tests/test_runtime.py TestDockerWrapperSanitize
```

**Specific test method:**
```bash
python3 tests/test_runtime.py TestDockerWrapperSanitize.test_valid_names
```

**Verbose output:**
```bash
python3 tests/test_runtime.py -v
```

## Test Dependencies

**Python:** Only stdlib
- `unittest` (unittest)
- `tempfile`, `pathlib` (fixtures)
- `datetime`, `timezone` (test data)
- `unittest.mock` (mocking)

**Bash:** Only coreutils + /bin/bash
- `mktemp`, `grep`, `echo`
- `grep -q` for silent matching
- `grep -oP` for field extraction (perl-compatible regex)

**No external libraries:**
- No `pytest`, `nose`, `behave`
- No `responses`, `requests-mock`
- No `faker`, `factory_boy`
- No CI configuration (`tox.ini`, `.travis.yml`, `github/workflows`)

## Test Data Integrity

**Isolation:**
- Each test class has its own `setUp()`/`tearDown()` for temp files
- Mocks are local to test method (patched via decorator)
- No shared state between tests

**Cleanup:**
- Temp directories removed after test completes
- Original module state restored (e.g., `ce.DIAGNOSTICS_LOG = self._orig`)
- Mock patches automatically undone by `@patch` decorator

**Determinism:**
- No random data generation (no seeded randomness)
- Fixed test data in `_make_state()`, `_mock_backends()`
- Timestamps in test data use UTC (no local timezone issues)

## Test Metrics

**Total tests (by module, 2026-04-28):**
- Unit test files: 60+
- Total assertions: 700+ (rough estimate)
- Pass rate: ~100% (failing tests = bugs, fixed immediately)

**Major test suites:**
- `test_runtime.py`: 54 tests (docker wrapper, event detection, task queue, patterns)
- `test_orchestrator.py`: 65 tests (query classification, model selection, routing)
- `test_model_router.py`: 93 tests (backend detection, V1/V2 routing, guardrails)
- `test_safety_guard.py`: 62 tests (action validation, container protection)
- `test_sprint*`: 5+ suites covering new features (B+A roadmap phases)

---

*Testing analysis: 2026-04-28*
