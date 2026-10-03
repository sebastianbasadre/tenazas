import threading
import time
from concurrent.futures import ThreadPoolExecutor

from inference_gate import InferenceSlot


def test_only_one_inference_runs_at_a_time():
    running = 0
    maximum = 0
    lock = threading.Lock()

    def call():
        nonlocal running, maximum
        with InferenceSlot(timeout=2):
            with lock:
                running += 1
                maximum = max(maximum, running)
            time.sleep(0.03)
            with lock:
                running -= 1

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda _: call(), range(10)))
    assert maximum == 1


def test_busy_slot_has_bounded_wait():
    import pytest

    with InferenceSlot(), pytest.raises(TimeoutError), InferenceSlot(timeout=0.01):
        raise AssertionError("must not acquire")
