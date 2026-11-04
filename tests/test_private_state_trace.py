import pytest

from tools.private_state_trace import classify, review


@pytest.mark.parametrize('syscall', ['openat', 'newfstatat', 'access', 'readlink'])
@pytest.mark.parametrize('outcome', ['0', '-1 EACCES (Permission denied)', '-1 ENOENT (No such file)'])
def test_even_silent_or_failed_legacy_lookup_is_evidence(syscall, outcome):
    line = f'{syscall}(AT_FDCWD, "/home/tenazas/runtime/state.json", 0) = {outcome}'
    assert classify(line) == 'legacy'


def test_private_replacement_dirfd_is_distinct_from_legacy():
    assert classify('newfstatat(3</home/tenazas/runtime/private-state>, "state.json", {}, 0) = 0') is None
    assert classify('newfstatat(3</home/tenazas/runtime>, "state.json", {}, 0) = 0') == 'unresolved-relative-state'


def test_missing_native_evidence_fails_closed(tmp_path):
    with pytest.raises(RuntimeError, match='Missing native traces'):
        review(tmp_path, 'observation')


def test_review_records_coordinates_without_dumping_logs(tmp_path):
    (tmp_path / 'observation.42').write_text('openat(AT_FDCWD, "/home/tenazas/runtime/state.json", 0) = -1 EACCES\n')
    result = review(tmp_path, 'observation')
    assert result == {'trace_files': 1, 'clean': False, 'hits': [
        {'trace': 'observation.42', 'line': 1, 'classification': 'legacy'}]}
