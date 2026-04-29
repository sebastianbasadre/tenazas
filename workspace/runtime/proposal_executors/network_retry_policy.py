"""
NetworkRetryPolicyExecutor

Adjusts retry / timeout / backoff parameters on an allow-listed wrapper
function. This is intentionally narrow: we only patch call-site kwargs
of known helper functions. We do NOT rewrite logic or add new imports.

patch_spec contract:
  {
    "operation": "adjust_retry_kwargs",
    "file": "runtime/network_cache.py",
    "function": "http_get_with_retry",    # target function name
    "kwargs": {                           # kwarg -> new value
      "timeout": 30,
      "retries": 5,
      "backoff": 1.5
    }
  }

The executor looks for the function definition, and for every kwarg in
'kwargs', patches its *default value* in the signature. If the kwarg is
not already a parameter of the function (i.e. no default to update), we
refuse. This keeps changes small and auditable — no new parameters, no
new code paths, no new imports.

Allowed kwargs and their sane bounds:
  timeout  : float, [0.5, 300]
  retries  : int,   [0, 10]
  backoff  : float, [1.0, 5.0]

Anything outside -> refuse.
"""
from __future__ import annotations

import ast
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

# Whitelist of kwargs we will patch, and their bounds.
_ALLOWED_KWARGS: Dict[str, Dict[str, Any]] = {
    "timeout":         {"type": (int, float), "min": 0.5,  "max": 300},
    "request_timeout": {"type": (int, float), "min": 0.5,  "max": 300},
    "retries":         {"type": int,          "min": 0,    "max": 10},
    "max_retries":     {"type": int,          "min": 0,    "max": 10},
    "backoff":         {"type": (int, float), "min": 1.0,  "max": 5.0},
    "backoff_factor":  {"type": (int, float), "min": 1.0,  "max": 5.0},
}

# Function names we'll touch. Anything else -> refuse. This prevents the
# executor from becoming a generic code rewriter.
_ALLOWED_FUNCTION_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"^http_.*"),
    re.compile(r"^fetch_.*"),
    re.compile(r"^request_.*"),
    re.compile(r"^get_with_retry$"),
    re.compile(r"^call_.*_api$"),
)


class NetworkRetryPolicyExecutor:
    TYPE = "network_retry_policy"

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

        err = self._validate_spec(spec)
        if err:
            return ApplyOutcome(ok=False, error=err)

        rel_file = spec["file"]
        fn_name = spec["function"]
        new_kwargs: Dict[str, Any] = dict(spec["kwargs"])

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
            return ApplyOutcome(ok=False, error=f"source does not parse: {e}")

        fn_nodes = [
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == fn_name
        ]
        if len(fn_nodes) == 0:
            return ApplyOutcome(
                ok=False,
                error=f"function {fn_name} not found in {rel_file}",
            )
        if len(fn_nodes) > 1:
            return ApplyOutcome(
                ok=False,
                error=f"ambiguous: {len(fn_nodes)} defs of {fn_name} in {rel_file}",
            )
        fn = fn_nodes[0]

        # Map kwarg name -> (default_node, index_into_defaults, arg_obj).
        kwarg_defaults = self._collect_kwarg_defaults(fn)
        for kw in new_kwargs:
            if kw not in kwarg_defaults:
                return ApplyOutcome(
                    ok=False,
                    error=(
                        f"kwarg '{kw}' is not a parameter of {fn_name} "
                        f"or has no default; refusing to add new parameters"
                    ),
                )

        # Validate types + bounds against allowlist.
        for kw, val in new_kwargs.items():
            allow = _ALLOWED_KWARGS[kw]
            if not isinstance(val, allow["type"]) or isinstance(val, bool):
                return ApplyOutcome(
                    ok=False,
                    error=f"kwarg '{kw}' has wrong type for allowlist",
                )
            if not (allow["min"] <= val <= allow["max"]):
                return ApplyOutcome(
                    ok=False,
                    error=(
                        f"kwarg '{kw}' value {val} outside bounds "
                        f"[{allow['min']}, {allow['max']}]"
                    ),
                )

        # Patch source line-by-line. For each target kwarg, replace its default
        # token in the signature. We do this via regex anchored to the function
        # def, to avoid touching call sites.
        new_src, replacements = self._patch_signature(
            src, fn, new_kwargs, kwarg_defaults,
        )
        if not replacements:
            return ApplyOutcome(
                ok=False,
                error="no defaults were changed; nothing to do",
            )

        # Parse + compile check.
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
                "function": fn_name,
                "changes": replacements,
            },
        )

    # ---- helpers ----------------------------------------------------------

    def _validate_spec(self, spec: Dict[str, Any]) -> Optional[str]:
        if spec.get("operation") != "adjust_retry_kwargs":
            return "operation must be 'adjust_retry_kwargs'"
        for k in ("file", "function", "kwargs"):
            if k not in spec:
                return f"missing key: {k}"
        if not isinstance(spec["file"], str) or not spec["file"].endswith(".py"):
            return "file must be a .py path"
        if not isinstance(spec["function"], str) or not spec["function"]:
            return "function must be a non-empty string"
        if not any(p.match(spec["function"]) for p in _ALLOWED_FUNCTION_PATTERNS):
            return f"function {spec['function']} not in allowlist"
        if not isinstance(spec["kwargs"], dict) or not spec["kwargs"]:
            return "kwargs must be a non-empty dict"
        for k in spec["kwargs"]:
            if k not in _ALLOWED_KWARGS:
                return f"kwarg '{k}' not in allowlist"
        return None

    @staticmethod
    def _collect_kwarg_defaults(
        fn: ast.FunctionDef,
    ) -> Dict[str, Tuple[ast.AST, int, ast.arg]]:
        """
        Returns mapping: kwarg_name -> (default_node, position, arg_obj).

        Covers both positional-or-keyword args with defaults AND kwonly args
        with defaults. Args without defaults are skipped — we don't invent
        defaults.
        """
        out: Dict[str, Tuple[ast.AST, int, ast.arg]] = {}
        # Positional-or-keyword with defaults:
        args = fn.args.args
        defaults = fn.args.defaults
        # Defaults align to the last N positional args.
        if defaults:
            first_default = len(args) - len(defaults)
            for i, d in enumerate(defaults):
                arg = args[first_default + i]
                out[arg.arg] = (d, first_default + i, arg)
        # Keyword-only args:
        for arg, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults):
            if d is None:
                continue
            out[arg.arg] = (d, -1, arg)
        return out

    @staticmethod
    def _patch_signature(
        src: str,
        fn: ast.FunctionDef,
        new_kwargs: Dict[str, Any],
        defaults: Dict[str, Tuple[ast.AST, int, ast.arg]],
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Replace defaults in the function signature using regex anchored
        between `def fn_name(` and the matching `)`. We compute the span from
        AST so we don't affect anything outside the signature.
        """
        # AST gives us fn.lineno and col_offset for 'def'. We need to find the
        # end of the signature — i.e. the ')' that closes the args before the
        # ':' that starts the body. We scan source from fn position.
        lines = src.splitlines(keepends=True)

        # Compute byte offset of the start of the function.
        start_off = sum(len(l) for l in lines[: fn.lineno - 1]) + fn.col_offset
        # Find matching paren.
        paren_start = src.find("(", start_off)
        if paren_start == -1:
            return src, []
        depth = 0
        paren_end = -1
        in_str = False
        str_ch = ""
        i = paren_start
        while i < len(src):
            c = src[i]
            if in_str:
                if c == "\\":
                    i += 2
                    continue
                if c == str_ch:
                    in_str = False
            else:
                if c in ('"', "'"):
                    in_str = True
                    str_ch = c
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        paren_end = i
                        break
            i += 1
        if paren_end == -1:
            return src, []

        sig = src[paren_start + 1 : paren_end]
        new_sig = sig
        changes: List[Dict[str, Any]] = []

        for kw, new_val in new_kwargs.items():
            default_node, _, arg_obj = defaults[kw]
            old_literal = _lit_from_node(default_node)
            if old_literal is _NO_LIT:
                # We can only handle literal defaults.
                continue
            new_literal = _fmt_literal(new_val)

            # Build a regex: kwarg name followed by optional annotation, then
            # '=' and the old literal. Anchored with lookbehind for comma or
            # opening paren start-of-signature.
            pat = re.compile(
                r"(?P<lead>(^|[,\n])\s*)"
                + re.escape(kw)
                + r"(?P<ann>\s*(?::[^=,]+)?)\s*="
                + r"(?P<ws>\s*)"
                + _literal_pattern(old_literal)
            )
            m = pat.search(new_sig)
            if not m:
                continue
            span = m.span()
            replaced = (
                new_sig[: span[0]]
                + m.group("lead")
                + kw
                + m.group("ann")
                + "="
                + m.group("ws")
                + new_literal
                + new_sig[span[1] :]
            )
            if replaced != new_sig:
                new_sig = replaced
                changes.append({
                    "kwarg": kw,
                    "old": old_literal,
                    "new": new_val,
                })

        if not changes:
            return src, []

        new_src = src[: paren_start + 1] + new_sig + src[paren_end:]
        return new_src, changes

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


# --- literal helpers -------------------------------------------------------

_NO_LIT = object()


def _lit_from_node(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return _NO_LIT


def _fmt_literal(v: Any) -> str:
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v.is_integer():
            return f"{v:.1f}"
        return repr(v)
    if isinstance(v, str):
        return repr(v)
    return repr(v)


def _literal_pattern(v: Any) -> str:
    if isinstance(v, bool):
        return r"\b" + ("True" if v else "False") + r"\b"
    if isinstance(v, int):
        return r"(?<![\w.])" + re.escape(str(v)) + r"(?!\w)"
    if isinstance(v, float):
        if v.is_integer():
            return (
                r"(?<![\w.])(?:"
                + re.escape(str(int(v)))
                + r"(?:\.0+)?|"
                + re.escape(repr(v))
                + r")(?!\w)"
            )
        return r"(?<![\w.])" + re.escape(repr(v)) + r"(?!\w)"
    if isinstance(v, str):
        return "(?:" + re.escape("'" + v + "'") + "|" + re.escape('"' + v + '"') + ")"
    return re.escape(repr(v))
