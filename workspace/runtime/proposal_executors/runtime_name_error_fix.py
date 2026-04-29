"""
RuntimeNameErrorFixExecutor

Fixes very narrow, well-understood NameError cases. The whole point of this
executor is to handle a handful of recurring paper cuts seen in production
(e.g. "_log is not defined" when someone adds a logging line without the
module-level logger, or a dropped stdlib import).

We only handle two patterns; anything else -> refuse.

patch_spec contract, variant A (missing module-level logger):
  {
    "operation": "add_module_logger",
    "file": "runtime/foo.py",
    "logger_name": "_log"                # must match the undefined name
  }

patch_spec contract, variant B (missing stdlib import):
  {
    "operation": "add_stdlib_import",
    "file": "runtime/foo.py",
    "module": "json"                     # must be in STDLIB_ALLOWLIST
  }

Anything else -> ApplyOutcome(ok=False, error="unsupported operation").

Why narrow:
  - A general 'fix NameError' is a refactor in disguise.
  - We don't invent code; we insert exactly one canonical line.
  - Adding a logger or a stdlib import cannot break a working file: if the
    compile check passes, the file is strictly more complete.

Safety rules:
  - The undefined name must actually be referenced somewhere in the file.
  - The canonical line we're about to insert must not already be present.
  - The resulting source must parse and compile.
"""
from __future__ import annotations

import ast
import py_compile
import re
import tempfile
from typing import Any, Dict, Optional, Set

from .base import (
    ApplyOutcome,
    Proposal,
    SandboxProtocol,
    read_file,
    safe_join,
)


STDLIB_ALLOWLIST: Set[str] = {
    "json", "os", "sys", "re", "time", "logging", "subprocess",
    "pathlib", "datetime", "typing", "collections", "itertools",
    "functools", "dataclasses", "enum", "contextlib", "hashlib",
    "shutil", "tempfile", "shlex", "math", "socket", "threading",
}

_VALID_LOGGER_NAME = re.compile(r"^_?[a-z][a-z0-9_]*$")


class RuntimeNameErrorFixExecutor:
    TYPE = "runtime_name_error_fix"

    def can_handle(self, proposal: Proposal) -> bool:
        return proposal.type == self.TYPE

    def apply(
        self,
        proposal: Proposal,
        sandbox: SandboxProtocol,
        branch: str,
        repo_root: str,
    ) -> ApplyOutcome:
        spec = dict(proposal.patch_spec or {})
        op = spec.get("operation")

        if op == "add_module_logger":
            return self._add_module_logger(spec, sandbox, branch, repo_root)
        if op == "add_stdlib_import":
            return self._add_stdlib_import(spec, sandbox, branch, repo_root)
        return ApplyOutcome(
            ok=False,
            error=f"unsupported operation for runtime_name_error_fix: {op!r}",
        )

    # ---- add_module_logger ------------------------------------------------

    def _add_module_logger(
        self,
        spec: Dict[str, Any],
        sandbox: SandboxProtocol,
        branch: str,
        repo_root: str,
    ) -> ApplyOutcome:
        rel_file = spec.get("file")
        logger_name = spec.get("logger_name")

        if not isinstance(rel_file, str) or not rel_file.endswith(".py"):
            return ApplyOutcome(ok=False, error="file must be a .py path")
        if not isinstance(logger_name, str) or not _VALID_LOGGER_NAME.match(logger_name):
            return ApplyOutcome(ok=False, error="invalid logger_name")

        try:
            abs_path = safe_join(repo_root, rel_file)
        except ValueError as e:
            return ApplyOutcome(ok=False, error=str(e))
        try:
            src = read_file(abs_path)
        except FileNotFoundError:
            return ApplyOutcome(ok=False, error=f"file not found: {rel_file}")

        # Source must parse already.
        try:
            tree = ast.parse(src, filename=rel_file)
        except SyntaxError as e:
            return ApplyOutcome(
                ok=False, error=f"source does not parse: {e}",
            )

        # Verify the logger name is actually referenced (otherwise nothing
        # to fix).
        if not self._name_is_referenced(tree, logger_name):
            return ApplyOutcome(
                ok=False,
                error=(
                    f"{logger_name!r} is never referenced in {rel_file}; "
                    f"no fix needed"
                ),
            )

        # Verify it's not already defined at module scope.
        if self._name_defined_at_module(tree, logger_name):
            return ApplyOutcome(
                ok=False,
                error=(
                    f"{logger_name} already defined at module scope in "
                    f"{rel_file}; no fix needed"
                ),
            )

        # Decide on the canonical insertion lines. We use stdlib logging so
        # we don't add a new dep.
        needs_logging_import = not self._has_import(tree, "logging")
        lines_to_insert = []
        if needs_logging_import:
            lines_to_insert.append("import logging")
        lines_to_insert.append(f"{logger_name} = logging.getLogger(__name__)")
        canonical = "\n".join(lines_to_insert)

        # Insert after the last top-level import (or at top if none).
        new_src = self._insert_after_imports(src, tree, canonical)
        if new_src == src:
            return ApplyOutcome(
                ok=False,
                error="could not determine insertion point",
            )

        # Compile check.
        try:
            ast.parse(new_src, filename=rel_file)
        except SyntaxError as e:
            return ApplyOutcome(
                ok=False, error=f"patched source does not parse: {e}",
            )
        err = self._py_compile_check(new_src, rel_file)
        if err:
            return ApplyOutcome(ok=False, error=err)

        sandbox.apply_file_change(branch, rel_file, new_src)
        return ApplyOutcome(
            ok=True,
            files_changed=[rel_file],
            details={
                "inserted": canonical.splitlines(),
                "logger_name": logger_name,
            },
        )

    # ---- add_stdlib_import ------------------------------------------------

    def _add_stdlib_import(
        self,
        spec: Dict[str, Any],
        sandbox: SandboxProtocol,
        branch: str,
        repo_root: str,
    ) -> ApplyOutcome:
        rel_file = spec.get("file")
        module = spec.get("module")

        if not isinstance(rel_file, str) or not rel_file.endswith(".py"):
            return ApplyOutcome(ok=False, error="file must be a .py path")
        if module not in STDLIB_ALLOWLIST:
            return ApplyOutcome(
                ok=False,
                error=f"module {module!r} not in stdlib allowlist",
            )

        try:
            abs_path = safe_join(repo_root, rel_file)
        except ValueError as e:
            return ApplyOutcome(ok=False, error=str(e))
        try:
            src = read_file(abs_path)
        except FileNotFoundError:
            return ApplyOutcome(ok=False, error=f"file not found: {rel_file}")

        try:
            tree = ast.parse(src, filename=rel_file)
        except SyntaxError as e:
            return ApplyOutcome(
                ok=False, error=f"source does not parse: {e}",
            )

        # Must be referenced as `<module>.` or bare `<module>` name.
        if not self._name_is_referenced(tree, module):
            return ApplyOutcome(
                ok=False,
                error=f"module {module!r} is never referenced in {rel_file}",
            )
        if self._has_import(tree, module):
            return ApplyOutcome(
                ok=False,
                error=f"module {module} already imported in {rel_file}",
            )

        canonical = f"import {module}"
        new_src = self._insert_after_imports(src, tree, canonical)
        if new_src == src:
            return ApplyOutcome(
                ok=False,
                error="could not determine insertion point",
            )

        try:
            ast.parse(new_src, filename=rel_file)
        except SyntaxError as e:
            return ApplyOutcome(
                ok=False, error=f"patched source does not parse: {e}",
            )
        err = self._py_compile_check(new_src, rel_file)
        if err:
            return ApplyOutcome(ok=False, error=err)

        sandbox.apply_file_change(branch, rel_file, new_src)
        return ApplyOutcome(
            ok=True,
            files_changed=[rel_file],
            details={"import": module},
        )

    # ---- shared helpers ---------------------------------------------------

    @staticmethod
    def _name_is_referenced(tree: ast.AST, name: str) -> bool:
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == name:
                return True
            # `logging.getLogger(...)` -> Attribute(value=Name('logging'))
            # handled by the Name check above.
        return False

    @staticmethod
    def _name_defined_at_module(tree: ast.AST, name: str) -> bool:
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == name:
                        return True
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and node.target.id == name:
                    return True
        return False

    @staticmethod
    def _has_import(tree: ast.AST, module: str) -> bool:
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == module or a.asname == module:
                        return True
            elif isinstance(node, ast.ImportFrom):
                if node.module == module:
                    return True
        return False

    @staticmethod
    def _insert_after_imports(src: str, tree: ast.AST, canonical: str) -> str:
        """
        Insert `canonical` after the last top-level import, or after any
        leading docstring if no imports exist.
        """
        last_import_line = 0
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                # end_lineno available on modern Python; fallback to lineno.
                end = getattr(node, "end_lineno", None) or node.lineno
                last_import_line = max(last_import_line, end)

        lines = src.splitlines(keepends=True)
        if last_import_line == 0:
            # Insert after docstring if any.
            insert_at = 0
            if (
                isinstance(tree, ast.Module)
                and tree.body
                and isinstance(tree.body[0], ast.Expr)
                and isinstance(getattr(tree.body[0], "value", None), ast.Constant)
                and isinstance(tree.body[0].value.value, str)
            ):
                insert_at = getattr(
                    tree.body[0], "end_lineno", tree.body[0].lineno
                )
        else:
            insert_at = last_import_line

        # Ensure blank line separation.
        prefix_lines = lines[:insert_at]
        suffix_lines = lines[insert_at:]

        # If the line right after insert_at is not blank, add a blank.
        needs_blank_after = bool(suffix_lines) and suffix_lines[0].strip() != ""

        # Build insertion.
        insertion = canonical.rstrip() + "\n"
        if needs_blank_after:
            insertion = insertion + "\n"

        return "".join(prefix_lines) + insertion + "".join(suffix_lines)

    @staticmethod
    def _py_compile_check(src: str, filename: str) -> Optional[str]:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".py", delete=False, encoding="utf-8",
        ) as f:
            f.write(src)
            tmp = f.name
        try:
            try:
                py_compile.compile(tmp, doraise=True)
                return None
            except py_compile.PyCompileError as e:
                return f"py_compile failed for {filename}: {e}"
        finally:
            import os
            try:
                os.unlink(tmp)
            except OSError:
                pass
