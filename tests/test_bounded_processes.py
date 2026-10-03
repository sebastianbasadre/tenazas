import multiprocessing
import time

from bounded_processes import bounded_calls
from process_lock import ProcessLock


def _sleep(seconds):
    time.sleep(seconds)
    return seconds


def _double(value):
    return value * 2


def test_deadline_terminates_blocked_call_and_keeps_fast_result():
    before = {p.pid for p in multiprocessing.active_children()}
    started = time.monotonic()
    outcomes = bounded_calls({"blocked": (_sleep, (30,)), "fast": (_double, (4,))}, timeout=2)
    assert time.monotonic() - started < 4
    assert outcomes["fast"] == {"ok": True, "value": 8}
    assert outcomes["blocked"]["timeout"]
    assert {p.pid for p in multiprocessing.active_children()} == before


def test_lock_is_exclusive_and_reusable(tmp_path):
    path = tmp_path / "execution.lock"
    owner, contender = ProcessLock(path), ProcessLock(path)
    assert owner.acquire()
    assert not contender.acquire()
    owner.release()
    assert contender.acquire()
    contender.release()
