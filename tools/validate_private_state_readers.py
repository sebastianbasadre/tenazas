"""Read-IO contract rehearsal for captured NAS sources, without importing services.

Uses the real load_json function and real path expressions from each consumer.
It does not execute full agents, real collectors, task actions or imports.
"""

import ast
import hashlib
import json
import os
import sys
from pathlib import Path

from tools.staging_private_json import save_json

ROOT = Path("/reader-view/runtime")
PRIVATE = ROOT / "private-state/state.json"
LEGACY = ROOT / "state.json"
NAMES = ["runtime", "background_agents", "goal_engine", "devin_lite", "infra_diagnostics",
         "benchmark_continuous", "world_model", "agent_security", "diagnostics_worker"]


def main():
    assert (os.geteuid(), os.getegid()) == (1026, 1026)
    assert not LEGACY.exists(), "Do not keep a compatibility alias"
    sentinel = {"ts": "staging-private", "containers": [{"name": "reader-sentinel", "up": True}]}
    save_json(PRIVATE, sentinel, uid=1026, gid=1026)
    original_runtime = ast.parse(Path("/reader-sources/runtime.py").read_text())
    function = next(n for n in original_runtime.body if isinstance(n, ast.FunctionDef) and n.name == "load_json")
    namespace = {"Path": Path, "json": json}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "captured-runtime-load_json", "exec"), namespace)
    load_json = namespace["load_json"]
    evidence = []
    for name in NAMES:
        source = Path("/reader-sources") / (name + ".py")
        tree = ast.parse(source.read_text())
        aliases = {}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.BinOp)
                    and isinstance(node.value.op, ast.Div)
                    and isinstance(node.value.right, ast.Constant) and node.value.right.value == "state.json"
                    and isinstance(node.value.left, ast.Name) and node.value.left.id == "BASE_DIR"):
                for target in node.targets:
                    if isinstance(target, ast.Name): aliases[target.id] = node.value
        assert aliases, name
        paths = {alias: eval(compile(ast.Expression(expr), str(source), "eval"), {"BASE_DIR": ROOT})
                 for alias, expr in aliases.items()}
        assert all(path == LEGACY for path in paths.values()), name
        migrated = {alias: PRIVATE for alias in paths}
        read_calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and node.args
                      and isinstance(node.func, ast.Name) and node.func.id in {"open", "load_json"}
                      and any(isinstance(n, ast.Name) and n.id in aliases for n in ast.walk(node.args[0]))]
        assert read_calls, name
        reads = 0
        for call in read_calls:
            # Evaluate only the actual read-path expression, not agent actions.
            expression = compile(ast.Expression(call.args[0]), str(source), "eval")
            original_path = eval(expression, paths)
            assert load_json(original_path, {}) == {}, "Negative control must miss the relocated state"
            new_path = eval(expression, migrated)
            assert Path(new_path) == PRIVATE
            if call.func.id == "open":
                with open(new_path) as stream: actual = json.load(stream)
            else: actual = load_json(new_path, {})
            assert actual == sentinel, name
            reads += 1
        evidence.append({"consumer": name, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                         "path_aliases": sorted(aliases), "read_expressions_tested": reads,
                         "legacy_path_missing_negative_control": True, "new_path_schema_ok": True})

    source = Path("/reader-sources/health_sweep.py")
    tree = ast.parse(source.read_text())
    probe = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_probe_memory_stores")
    context = {"BASE_DIR": ROOT, "_ok": lambda *args: args[-1], "_warn": lambda *args: args[-1]}
    exec(compile(ast.Module(body=[probe], type_ignores=[]), "health-original", "exec"), context)
    before = context["_probe_memory_stores"]()
    assert "state.json" not in before["sizes"], "Demonstrate the silent missing-reader bug"

    class HealthPath(ast.NodeTransformer):
        def visit_Assign(self, node):
            if any(isinstance(t, ast.Name) and t.id == "p" for t in node.targets):
                node.value = ast.IfExp(test=ast.Compare(left=ast.Name(id="name", ctx=ast.Load()),
                    ops=[ast.Eq()], comparators=[ast.Constant("state.json")]),
                    body=ast.Name(id="PRIVATE", ctx=ast.Load()), orelse=node.value)
            return node
    probe = ast.fix_missing_locations(HealthPath().visit(probe))
    context["PRIVATE"] = PRIVATE
    exec(compile(ast.Module(body=[probe], type_ignores=[]), "health-migrated", "exec"), context)
    after = context["_probe_memory_stores"]()
    assert after["sizes"]["state.json"] == PRIVATE.stat().st_size
    evidence.append({"consumer": "health_sweep", "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                     "unmodified_probe_silently_omits_state": True, "migrated_probe_reports_real_size": True})
    print(json.dumps({"consumer_contracts_passed": len(evidence), "failed": 0,
                      "legacy_alias_present": False, "consumers": evidence,
                      "scope": "captured read expressions + health probe; not full service execution"}))


if __name__ == "__main__":
    main()
