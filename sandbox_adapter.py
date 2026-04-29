"""
Sandbox adapter layer.

Wraps autofix_sandbox.py when importable. Otherwise falls back to a local
git-backed implementation using the 'git' binary directly. Tests can also
inject an InMemorySandbox.

The adapter's job is to present a stable, narrow API to executors so they
don't depend on whichever internal autofix signature is current.

If the real autofix_sandbox.py exposes different function names, adjust
_AutofixAdapter below. It is the single point of integration.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .base import SandboxProtocol, path_is_protected


# ---- Attempt to import autofix_sandbox at module load, cache outcome ------

_AUTOFIX_MODULE = None
_AUTOFIX_IMPORT_ERROR: Optional[str] = None
try:  # pragma: no cover - depends on runtime environment
    from runtime import autofix_sandbox as _autofix_sandbox_mod  # type: ignore
    _AUTOFIX_MODULE = _autofix_sandbox_mod
except Exception as e:  # pragma: no cover
    try:
        import autofix_sandbox as _autofix_sandbox_mod  # type: ignore
        _AUTOFIX_MODULE = _autofix_sandbox_mod
    except Exception as e2:
        _AUTOFIX_IMPORT_ERROR = f"{type(e).__name__}: {e}; fallback: {e2}"


def get_sandbox(repo_root: str, prefer: str = "auto") -> SandboxProtocol:
    """
    Pick a sandbox implementation.

    prefer:
      "auto"    - autofix if available, else git
      "autofix" - force autofix (raises if unavailable)
      "git"     - force local git adapter
      "memory"  - in-memory, for tests only
    """
    if prefer == "memory":
        return InMemorySandbox(repo_root)
    if prefer == "autofix":
        if _AUTOFIX_MODULE is None:
            raise RuntimeError(
                f"autofix_sandbox not importable: {_AUTOFIX_IMPORT_ERROR}"
            )
        return _AutofixAdapter(repo_root, _AUTOFIX_MODULE)
    if prefer == "git":
        return GitSandbox(repo_root)
    # auto
    if _AUTOFIX_MODULE is not None:
        try:
            return _AutofixAdapter(repo_root, _AUTOFIX_MODULE)
        except Exception:
            return GitSandbox(repo_root)
    return GitSandbox(repo_root)


# ---------------------------------------------------------------------------
# Adapter for the real autofix_sandbox module.
# ---------------------------------------------------------------------------


class _AutofixAdapter:
    """
    Wraps the real autofix_sandbox. Assumes the module exposes (at least one
    viable mapping for each) the following capabilities:

        - create_branch(name) -> str
        - write_file(branch, path, content)  OR  apply_file_change(...)
        - get_diff(branch) / diff(branch) -> str
        - files_changed(branch) -> list[str]
        - validate_diff(branch, max_lines, protected) -> Optional[str]
        - run_tests(cmd, cwd) -> dict
        - merge(branch, message) / merge_to_main(...)
        - rollback(branch) / revert(branch)

    We probe attributes rather than hard-call specific names. If a capability
    is missing, we fall through to git-level ops so the adapter still works.
    """

    def __init__(self, repo_root: str, module: Any):
        self.repo_root = os.path.abspath(repo_root)
        self.m = module
        self._git = GitSandbox(repo_root)  # fallback for missing capabilities

    # helper
    def _call(self, names: List[str], *args, **kwargs):
        for n in names:
            fn = getattr(self.m, n, None)
            if callable(fn):
                return fn(*args, **kwargs)
        raise AttributeError(f"autofix_sandbox missing any of: {names}")

    def create_branch(self, name: str) -> str:
        try:
            return self._call(["create_branch", "new_branch", "start_branch"], name)
        except AttributeError:
            return self._git.create_branch(name)

    def apply_file_change(self, branch: str, path: str, new_content: str) -> None:
        try:
            return self._call(
                ["apply_file_change", "write_file", "put_file"],
                branch, path, new_content,
            )
        except AttributeError:
            return self._git.apply_file_change(branch, path, new_content)

    def diff_for_branch(self, branch: str) -> str:
        try:
            return self._call(["diff_for_branch", "get_diff", "diff"], branch)
        except AttributeError:
            return self._git.diff_for_branch(branch)

    def diff_line_count(self, branch: str) -> int:
        try:
            return int(self._call(["diff_line_count"], branch))
        except (AttributeError, TypeError, ValueError):
            return self._git.diff_line_count(branch)

    def files_changed(self, branch: str) -> List[str]:
        try:
            return list(self._call(["files_changed", "changed_files"], branch))
        except AttributeError:
            return self._git.files_changed(branch)

    def validate_diff(
        self, branch: str, max_lines: int, protected_files: List[str]
    ) -> Optional[str]:
        try:
            return self._call(
                ["validate_diff", "verify_diff"],
                branch, max_lines, protected_files,
            )
        except AttributeError:
            return self._git.validate_diff(branch, max_lines, protected_files)

    def run_tests(self, test_command: str, cwd: Optional[str] = None) -> Dict[str, Any]:
        try:
            return self._call(["run_tests", "test"], test_command, cwd)
        except AttributeError:
            return self._git.run_tests(test_command, cwd)

    def merge_to_main(self, branch: str, message: str) -> str:
        try:
            return self._call(["merge_to_main", "merge"], branch, message)
        except AttributeError:
            return self._git.merge_to_main(branch, message)

    def rollback(self, branch: str) -> None:
        try:
            return self._call(["rollback", "revert", "drop_branch"], branch)
        except AttributeError:
            return self._git.rollback(branch)


# ---------------------------------------------------------------------------
# Git-backed adapter (fallback / local dev).
# ---------------------------------------------------------------------------


class GitSandbox:
    """
    Minimal branch-and-merge workflow using the git CLI. Safe for local
    repos; DOES use the working tree, so call from within the repo.

    Guarantees:
      - Never calls git with shell=True.
      - Never force-pushes.
      - Only touches paths inside repo_root.
    """

    def __init__(self, repo_root: str):
        self.repo_root = os.path.abspath(repo_root)
        if not os.path.isdir(os.path.join(self.repo_root, ".git")):
            # Don't hard-fail; tests may run in non-git dirs and use InMemory.
            # But flag it.
            self._has_git = False
        else:
            self._has_git = True

    # ---- internals --------------------------------------------------------

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        if not self._has_git:
            raise RuntimeError(f"not a git repo: {self.repo_root}")
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            check=check,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def _current_branch(self) -> str:
        return self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    def _main_branch(self) -> str:
        # Best-effort: prefer 'main', then 'master'.
        for b in ("main", "master"):
            r = self._git("rev-parse", "--verify", f"refs/heads/{b}", check=False)
            if r.returncode == 0:
                return b
        return self._current_branch()

    # ---- API --------------------------------------------------------------

    def create_branch(self, name: str) -> str:
        base = self._main_branch()
        # Ensure name is unique-ish.
        self._git("checkout", base)
        self._git("checkout", "-b", name)
        return name

    def apply_file_change(self, branch: str, path: str, new_content: str) -> None:
        # Assume we're on branch; don't auto-switch to avoid surprises.
        current = self._current_branch()
        if current != branch:
            self._git("checkout", branch)
        abs_path = os.path.abspath(os.path.join(self.repo_root, path))
        if not abs_path.startswith(self.repo_root + os.sep):
            raise ValueError(f"path escapes repo: {path}")
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        self._git("add", path)

    def _ensure_commit(self, branch: str) -> None:
        # If there are staged changes, commit them. No-op if clean.
        r = self._git("status", "--porcelain", check=False)
        if r.stdout.strip():
            self._git(
                "-c", "user.email=tenazas@local",
                "-c", "user.name=tenazas-executor",
                "commit", "-m", f"proposal-exec: staged changes on {branch}",
            )

    def diff_for_branch(self, branch: str) -> str:
        self._ensure_commit(branch)
        base = self._main_branch()
        r = self._git("diff", f"{base}...{branch}", check=False)
        return r.stdout

    def diff_line_count(self, branch: str) -> int:
        return len(self.diff_for_branch(branch).splitlines())

    def files_changed(self, branch: str) -> List[str]:
        self._ensure_commit(branch)
        base = self._main_branch()
        r = self._git("diff", "--name-only", f"{base}...{branch}", check=False)
        return [l.strip() for l in r.stdout.splitlines() if l.strip()]

    def validate_diff(
        self, branch: str, max_lines: int, protected_files: List[str]
    ) -> Optional[str]:
        lines = self.diff_line_count(branch)
        if lines > max_lines:
            return f"diff too large: {lines} > {max_lines}"
        for f in self.files_changed(branch):
            if path_is_protected(f, protected_files):
                return f"protected file touched: {f}"
        return None

    def run_tests(self, test_command: str, cwd: Optional[str] = None) -> Dict[str, Any]:
        if not test_command:
            return {"passed": True, "returncode": 0, "stdout": "", "stderr": "",
                    "skipped": True}
        # IMPORTANT: never shell=True with untrusted input. We split with shlex
        # and the command template comes from the executor, not the proposal.
        argv = shlex.split(test_command)
        r = subprocess.run(
            argv,
            cwd=cwd or self.repo_root,
            capture_output=True,
            text=True,
            timeout=300,
        )
        return {
            "passed": r.returncode == 0,
            "returncode": r.returncode,
            "stdout": r.stdout[-4000:],
            "stderr": r.stderr[-4000:],
        }

    def merge_to_main(self, branch: str, message: str) -> str:
        self._ensure_commit(branch)
        base = self._main_branch()
        self._git("checkout", base)
        # --no-ff for a real merge commit; keeps audit trail.
        self._git("merge", "--no-ff", "-m", message, branch)
        sha = self._git("rev-parse", "HEAD").stdout.strip()
        return sha

    def rollback(self, branch: str) -> None:
        base = self._main_branch()
        current = self._current_branch()
        if current == branch:
            self._git("checkout", base)
        # Delete branch; discard work (-D because it may be unmerged).
        self._git("branch", "-D", branch, check=False)


# ---------------------------------------------------------------------------
# In-memory sandbox for tests. Zero git calls, no disk mutation outside tmp.
# ---------------------------------------------------------------------------


@dataclass
class _MemBranch:
    name: str
    files: Dict[str, str] = field(default_factory=dict)
    base_files: Dict[str, str] = field(default_factory=dict)
    merged: bool = False
    merge_sha: Optional[str] = None


class InMemorySandbox:
    """
    Pure-Python sandbox. Simulates branches, diffs, merges without touching
    any real git repo. Used exclusively by tests.

    The "main" content is initialized from repo_root's actual files (so you
    can set up a tmp dir with seed files and then branch off it).
    """

    def __init__(self, repo_root: str):
        self.repo_root = os.path.abspath(repo_root)
        self.main: Dict[str, str] = {}
        self.branches: Dict[str, _MemBranch] = {}
        self.test_hook: Optional[Any] = None
        self.merge_counter = 0
        self.fail_merge = False
        self._seed_from_disk()

    def _seed_from_disk(self) -> None:
        if not os.path.isdir(self.repo_root):
            return
        for root, _, files in os.walk(self.repo_root):
            for name in files:
                abs_p = os.path.join(root, name)
                rel = os.path.relpath(abs_p, self.repo_root)
                try:
                    with open(abs_p, "r", encoding="utf-8") as f:
                        self.main[rel] = f.read()
                except Exception:
                    pass

    def create_branch(self, name: str) -> str:
        if name in self.branches:
            name = f"{name}-{int(time.time() * 1000)}"
        self.branches[name] = _MemBranch(
            name=name,
            files=dict(self.main),
            base_files=dict(self.main),
        )
        return name

    def apply_file_change(self, branch: str, path: str, new_content: str) -> None:
        if branch not in self.branches:
            raise KeyError(f"unknown branch: {branch}")
        self.branches[branch].files[path] = new_content

    def diff_for_branch(self, branch: str) -> str:
        b = self.branches[branch]
        lines: List[str] = []
        for path in sorted(set(b.files) | set(b.base_files)):
            before = b.base_files.get(path, "")
            after = b.files.get(path, "")
            if before == after:
                continue
            lines.append(f"--- a/{path}")
            lines.append(f"+++ b/{path}")
            for line in before.splitlines():
                lines.append(f"-{line}")
            for line in after.splitlines():
                lines.append(f"+{line}")
        return "\n".join(lines)

    def diff_line_count(self, branch: str) -> int:
        d = self.diff_for_branch(branch)
        if not d:
            return 0
        return len(d.splitlines())

    def files_changed(self, branch: str) -> List[str]:
        b = self.branches[branch]
        out: List[str] = []
        for path in sorted(set(b.files) | set(b.base_files)):
            if b.files.get(path, "") != b.base_files.get(path, ""):
                out.append(path)
        return out

    def validate_diff(
        self, branch: str, max_lines: int, protected_files: List[str]
    ) -> Optional[str]:
        lines = self.diff_line_count(branch)
        if lines > max_lines:
            return f"diff too large: {lines} > {max_lines}"
        for f in self.files_changed(branch):
            if path_is_protected(f, protected_files):
                return f"protected file touched: {f}"
        return None

    def run_tests(self, test_command: str, cwd: Optional[str] = None) -> Dict[str, Any]:
        # If a test_hook is set, delegate. Otherwise pass.
        if self.test_hook is not None:
            return self.test_hook(test_command, cwd)
        return {"passed": True, "returncode": 0, "stdout": "", "stderr": ""}

    def merge_to_main(self, branch: str, message: str) -> str:
        if self.fail_merge:
            raise RuntimeError("simulated merge failure")
        b = self.branches[branch]
        self.main.update(b.files)
        # Drop any deleted files (none in our model currently).
        b.merged = True
        self.merge_counter += 1
        sha = f"deadbeef{self.merge_counter:04d}"
        b.merge_sha = sha
        return sha

    def rollback(self, branch: str) -> None:
        # Simulate by dropping the branch.
        self.branches.pop(branch, None)


def get_import_error() -> Optional[str]:
    return _AUTOFIX_IMPORT_ERROR
