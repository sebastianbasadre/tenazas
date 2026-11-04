"""Canonical private raw state path; no legacy alias or fallback."""

from pathlib import Path


def private_state_path(runtime_directory):
    return Path(runtime_directory) / "private-state" / "state.json"
