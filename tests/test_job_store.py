from job_store import JobStore


def test_duplicate_completed_job_reuses_result(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    assert store.claim("infra", "slot-1", 10) is None
    store.finish("slot-1", {"ok": True, "job": "infra"})
    assert store.claim("infra", "slot-1", 10)["deduplicated"]
    assert store.summary()["counts"] == {"succeeded": 1}


def test_concurrent_job_and_cross_job_key_are_rejected(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    assert store.claim("infra", "slot-1", 10) is None
    assert store.claim("infra", "slot-2", 10)["conflict"]
    assert store.claim("finance", "slot-1", 10)["conflict"]


def test_crashed_job_requires_review_before_retry(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    assert store.claim("finance", "slot-1", -100) is None
    assert store.claim("finance", "slot-2", 10)["conflict"]
    assert store.summary()["counts"] == {"interrupted": 1}
