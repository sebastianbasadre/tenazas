"""Build two exact source-only bundles locally; no deploy, credentials or git operations."""

import argparse
import hashlib
import json
import tarfile
from pathlib import Path

COMMON = [
    "workspace/__init__.py",
    "workspace/runtime/__init__.py",
    "workspace/runtime/mcp_snapshot_contract.py",
    "workspace/runtime/mcp_source_contract.py",
    "workspace/runtime/mcp_media_contract.py",
    "workspace/runtime/mcp_media_reader.py",
]
PAYLOADS = {
    "nas-sources": COMMON
    + [
        "workspace/runtime/mcp_sanitized_sources.py",
        "tools/mcp_nas_inventory.py",
        "deploy/mcp-v2/tenazas-mcp-sources.service",
    ],
    "mcp-v2": COMMON
    + [
        "workspace/runtime/mcp_readonly_adapter.py",
        "workspace/runtime/mcp_readonly_gateway.py",
        "workspace/runtime/mcp_cache_policy.py",
        "workspace/runtime/mcp_snapshot_producer.py",
        "workspace/runtime/mcp_media_producer.py",
        "deploy/mcp-v2/tenazas-mcp-gateway.conf",
        "deploy/mcp-v2/tenazas-mcp-snapshot.service",
        "deploy/mcp-v2/tenazas-mcp-snapshot.timer",
    ],
}


def build(root, destination, kind):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination == root or root in destination.parents:
        raise ValueError("Package output must be outside checkout")
    if kind not in PAYLOADS:
        raise ValueError("Unknown package")
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / (kind + ".tar.gz")
    manifest_path = destination / (kind + ".manifest.json")
    if archive.exists() or manifest_path.exists():
        raise ValueError("Refuse package overwrite")
    hashes = {}
    for name in PAYLOADS[kind]:
        path = root / name
        if not path.is_file() or path.is_symlink() or path.resolve() != path:
            raise ValueError("Unsafe package entry")
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    with tarfile.open(archive, "w:gz") as output:
        for name in PAYLOADS[kind]:
            archive_name = (
                "app/" + name if kind == "mcp-v2" and name.startswith("workspace/") else name
            )
            output.add(root / name, arcname=archive_name, recursive=False)
    manifest_path.write_text(
        json.dumps(
            {"files": hashes, "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return archive, manifest_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=PAYLOADS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for result in build(Path(__file__).resolve().parents[1], args.output, args.kind):
        print(result)
