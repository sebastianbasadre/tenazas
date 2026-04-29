"""
ThresholdChangeExecutor

Handles proposals of type "threshold_change" and "guardrail_tuning".

patch_spec contract (required keys):
  {
    "operation": "replace_constant",
    "file": "runtime/model_router.py",       # relative to repo_root
    "constant_name": "HEAVY_COOLDOWN_SEC",
    "old_value": 120,
    "new_value": 60,
    "value_type": "int"                      # int | float | bool | str
  }

Safety rules:
  - constant_name must match ^[A-Z][A-Z0-9_]*$ (module-level uppercase).
  - The file must contain EXACTLY ONE top-level assignment that matches.
  - old_value must match what's currently in the file.
  - new_value must be the correct type.
  - For numeric types, the new value must be within sane bounds (>0 for
    timers, <= 10x old value, >= 0.1x old value). That prevents a bad
    proposal from disabling a cooldown entirely or setting it to years.
  - After patching, the file must still compile (py_compile).

Anything that fails any of these checks -> ApplyOutcome(ok=False).
"""
from __future__ import annotations

import ast
import io
import py_compile
import re
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from .base import (
    ApplyOutcome,
    Proposal,
    SandboxProtocol,
    read_file,
    safe_join,
)


_CONST_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


# Sanity bounds per value_type. Hard upper/lower and bounded ratios.
_BOUNDS: Dict[str, Dict[str, Any]] = {
    "int": {
        "min_abs": 1,            # at least 1 (avoid disabling timers)
        "max_abs": 86_400 * 30,  # 30 days in seconds; generous ceiling
        "min_ratio": 0.1,
        "max_ratio": 10.0,
    },
    "float": {
        "min_abs": 0.001,
        "max_abs": 1e7,
        "min_ratio": 0.1,
        "max_ratio": 10.0,
    },
    # bool / str have no numeric bounds; handled in validate_value.
}


class ThresholdChangeExecutor:
    TYPE = "threshold_change"
    ALIASES = ("guardrail_tuning",)

    def can_handle(self, proposal: Proposal) -> bool:
        return proposal.type in (self.TYPE,) + self.ALIASES

    # ----------------------------------------------------------------------

    def apply(
        self,
        proposal: Proposal,
        sandbox: SandboxProtocol,
        branch: str,
        repo_root: str,
    ) -> ApplyOutcome:
        spec = dict(proposal.patch_spec or {})
        err = self._validate_spec(spec)
        if err:
            return ApplyOutcome(ok=False, error=f"invalid patch_spec: {err}")

        rel_file = spec["file"]
        const = spec["constant_name"]
        old_value = spec["old_value"]
        new_value = spec["new_value"]
        value_type = spec["value_type"]

        # Bounds check.
        err = self._validate_value(old_value, new_value, value_type)
        if err:
            return ApplyOutcome(ok=False, error=err)

        # Read source.
        try:
            abs_path = safe_join(repo_root, rel_file)
        except ValueError as e:
            return ApplyOutcome(ok=False, error=str(e))

        try:
            src = read_file(abs_path)
        except FileNotFoundError:
            return ApplyOutcome(ok=False, error=f"file not found: {rel_file}")

        # AST-verify there's exactly one top-level assignment to this constant.
        try:
            tree = ast.parse(src, filename=rel_file)
        except SyntaxError as e:
            return ApplyOutcome(ok=False, error=f"source does not parse: {e}")

        matches = self._find_constant_assignments(tree, const)
        if len(matches) == 0:
            return ApplyOutcome(
                ok=False,
                error=f"no top-level assignment '{const}' found in {rel_file}",
            )
        if len(matches) > 1:
            return ApplyOutcome(
                ok=False,
                error=(
                    f"ambiguous: {len(matches)} top-level assignments "
                    f"of '{const}' in {rel_file}"
                ),
            )

        # Extract current value literal and verify it matches old_value.
        current_value, value_node = matches[0]
        if not self._values_equal(current_value, old_value, value_type):
            return ApplyOutcome(
                ok=False,
                error=(
                    f"current value of {const} is {current_value!r}, "
                    f"not {old_value!r}; refusing to patch"
                ),
            )

        # Build the new source using a targeted replacement. We use the line
        # number from AST and a regex anchored to the start of the line to
        # avoid touching incidental occurrences (comments, other assignments).
        new_src, replaced = self._replace_constant_source(
            src, const, old_value, new_value, value_type, value_node.lineno,
        )
        if not replaced:
            return ApplyOutcome(
                ok=False,
                error=f"could not locate assignment line for {const}",
            )

        # Re-parse to confirm the file still compiles.
        try:
            ast.parse(new_src, filename=rel_file)
        except SyntaxError as e:
            return ApplyOutcome(
                ok=False, error=f"patched source does not parse: {e}",
            )

        # py_compile for stronger check (bytecode compile, catches edge cases).
        err = self._py_compile_check(new_src, rel_file)
        if err:
            return ApplyOutcome(ok=False, error=err)

        # Apply via sandbox.
        sandbox.apply_file_change(branch, rel_file, new_src)

        return ApplyOutcome(
            ok=True,
            files_changed=[rel_file],
            details={
                "constant": const,
                "old_value": old_value,
                "new_value": new_value,
            },
        )

    # ---- helpers ----------------------------------------------------------

    def _validate_spec(self, spec: Dict[str, Any]) -> Optional[str]:
        if spec.get("operation") != "replace_constant":
            return "operation must be 'replace_constant'"
        for k in ("file", "constant_name", "old_value", "new_value", "value_type"):
            if k not in spec:
                return f"missing key: {k}"
        if not isinstance(spec["file"], str) or not spec["file"].endswith(".py"):
            return "file must be a .py path"
        if not _CONST_NAME_RE.match(str(spec["constant_name"])):
            return f"constant_name not valid: {spec['constant_name']!r}"
        if spec["value_type"] not in ("int", "float", "bool", "str"):
            return f"unsupported value_type: {spec['value_type']}"
        if not self._type_matches(spec["old_value"], spec["value_type"]):
            return f"old_value type mismatch for {spec['value_type']}"
        if not self._type_matches(spec["new_value"], spec["value_type"]):
            return f"new_value type mismatch for {spec['value_type']}"
        return None

    @staticmethod
    def _type_matches(v: Any, t: str) -> bool:
        if t == "int":
            return isinstance(v, int) and not isinstance(v, bool)
        if t == "float":
            return isinstance(v, (int, float)) and not isinstance(v, bool)
        if t == "bool":
            return isinstance(v, bool)
        if t == "str":
            return isinstance(v, str)
        return False

    def _validate_value(self, old: Any, new: Any, t: str) -> Optional[str]:
        if t in ("bool", "str"):
            if old == new:
                return "new_value equals old_value; nothing to do"
            if t == "str" and len(str(new)) > 256:
                return "new string value too long (>256 chars)"
            return None

        b = _BOUNDS[t]
        if new == old:
            return "new_value equals old_value; nothing to do"
        if not (b["min_abs"] <= abs(new) <= b["max_abs"]):
            return f"new_value {new} out of absolute bounds {b['min_abs']}..{b['max_abs']}"
        if old == 0:
            return "old_value is 0; ratio bounds undefined, refusing"
        ratio = new / old
        if not (b["min_ratio"] <= ratio <= b["max_ratio"]):
            return (
                f"ratio {ratio:.2f}x outside safe range "
                f"[{b['min_ratio']}, {b['max_ratio']}]"
            )
        return None

    @staticmethod
    def _find_constant_assignments(
        tree: ast.AST, name: str,
    ) -> List[Tuple[Any, ast.AST]]:
        """
        Return list of (literal_value, value_node) for every top-level
        'NAME = <constant literal>' assignment.
        Annotations like 'NAME: int = 5' also supported.
        """
        out: List[Tuple[Any, ast.AST]] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                if len(node.targets) != 1:
                    continue
                tgt = node.targets[0]
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    val = _safe_literal(node.value)
                    if val is not _SENTINEL:
                        out.append((val, node.value))
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and node.target.id == name:
                    if node.value is None:
                        continue
                    val = _safe_literal(node.value)
                    if val is not _SENTINEL:
                        out.append((val, node.value))
        return out

    @staticmethod
    def _values_equal(a: Any, b: Any, t: str) -> bool:
        if t == "float":
            try:
                return abs(float(a) - float(b)) < 1e-9
            except (TypeError, ValueError):
                return False
        return a == b

    def _replace_constant_source(
        self,
        src: str,
        name: str,
        old_value: Any,
        new_value: Any,
        value_type: str,
        lineno: int,
    ) -> Tuple[str, bool]:
        """
        Replace the assignment on the specific line. We target the line
        reported by AST, then re-verify the literal within it.
        """
        lines = src.splitlines(keepends=True)
        idx = lineno - 1
        if idx < 0 or idx >= len(lines):
            return src, False

        line = lines[idx]
        new_literal = self._format_literal(new_value, value_type)
        old_literal_re = self._literal_regex(old_value, value_type)

        # Match 'NAME<ws><optional :type><ws>=<ws><literal>' or just the RHS.
        # To be robust, replace the first occurrence of the old literal that
        # appears after an '=' on this line.
        eq_pos = line.find("=")
        if eq_pos == -1:
            # AnnAssign's ast.value lineno can differ from the assignment's
            # source line when multiline. Fall back to linear scan.
            return self._replace_multiline(
                src, name, old_literal_re, new_literal,
            )

        prefix = line[: eq_pos + 1]
        rest = line[eq_pos + 1 :]
        new_rest, nsub = old_literal_re.subn(new_literal, rest, count=1)
        if nsub != 1:
            return self._replace_multiline(src, name, old_literal_re, new_literal)
        lines[idx] = prefix + new_rest
        return "".join(lines), True

    @staticmethod
    def _replace_multiline(
        src: str, name: str, old_literal_re: "re.Pattern[str]", new_literal: str,
    ) -> Tuple[str, bool]:
        """
        Fallback: find 'NAME ... = <old>' anywhere, replace first hit.
        Anchored to start-of-line for the NAME to avoid matching suffixes.
        """
        name_re = re.compile(
            r"(?m)^(\s*" + re.escape(name) + r"\s*(?::[^=]+)?\s*=\s*)(.+)$"
        )
        m = name_re.search(src)
        if not m:
            return src, False
        lhs, rhs = m.group(1), m.group(2)
        new_rhs, nsub = old_literal_re.subn(new_literal, rhs, count=1)
        if nsub != 1:
            return src, False
        start, end = m.span()
        new_src = src[:start] + lhs + new_rhs + src[end:]
        return new_src, True

    @staticmethod
    def _format_literal(v: Any, t: str) -> str:
        if t == "int":
            return str(int(v))
        if t == "float":
            # Avoid scientific notation for readability.
            if float(v).is_integer():
                return f"{float(v):.1f}"
            return repr(float(v))
        if t == "bool":
            return "True" if v else "False"
        if t == "str":
            return repr(str(v))
        return repr(v)

    @staticmethod
    def _literal_regex(v: Any, t: str) -> "re.Pattern[str]":
        if t == "int":
            # Match the number as a standalone token.
            return re.compile(r"(?<![\w.])" + re.escape(str(int(v))) + r"(?!\w)")
        if t == "float":
            # Accept variants 60, 60.0, 60.00
            f = float(v)
            if f.is_integer():
                alt = rf"(?:{int(f)}(?:\.0+)?|{f:.1f})"
            else:
                alt = re.escape(repr(f))
            return re.compile(r"(?<![\w.])" + alt + r"(?!\w)")
        if t == "bool":
            return re.compile(r"\b" + ("True" if v else "False") + r"\b")
        if t == "str":
            # Accept either ' or " quoting.
            s = str(v)
            single = re.escape("'" + s + "'")
            double = re.escape('"' + s + '"')
            return re.compile("(?:" + single + "|" + double + ")")
        return re.compile(re.escape(str(v)))

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


# Sentinel for "not a literal we can evaluate safely".
_SENTINEL = object()


def _safe_literal(node: ast.AST) -> Any:
    """
    Like ast.literal_eval but returns _SENTINEL instead of raising. Only
    returns Python primitives; will NOT evaluate names, calls, attr access.
    """
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return _SENTINEL
