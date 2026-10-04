import ast
from pathlib import Path

from workspace.runtime.state_paths import private_state_path


def test_route_is_fixed_even_if_legacy_exists(tmp_path, monkeypatch):
    (tmp_path / 'state.json').write_text('{"legacy":true}')
    monkeypatch.setenv('TENAZAS_STATE_FILE', str(tmp_path / 'state.json'))
    assert private_state_path(tmp_path) == tmp_path / 'private-state/state.json'


def test_all_known_raw_state_consumers_use_shared_route():
    root = Path(__file__).parents[1] / 'workspace/runtime'
    consumers = ['runtime.py', 'agent_security.py', 'background_agents.py',
                 'benchmark_continuous.py', 'devin_lite.py', 'goal_engine.py',
                 'health_sweep.py', 'infra_diagnostics.py', 'world_model.py',
                 'workers/diagnostics_worker.py']
    for name in consumers:
        tree = ast.parse((root / name).read_text(encoding='utf-8'))
        assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id == 'private_state_path' for node in ast.walk(tree)), name
        assert not any(isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
                       and isinstance(node.right, ast.Constant) and node.right.value == 'state.json'
                       for node in ast.walk(tree)), name
