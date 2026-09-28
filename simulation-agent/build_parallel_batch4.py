"""Build the selected no-flag four-worker T3 image from the exact release parent."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from build_parallel_batch import BASE_ID, BASE_TAG, EVIDENCE, SOURCE, inspect

ROOT = SOURCE.parent
CONTEXT = ROOT / ".validation" / "t3-parallel-batch4-context-20260927"
TAG = "simulation-agent:parallel-batch4-20260927"
DIAGNOSTIC_SOURCE_SHA = "6726873220f3a928a39910273786769d3630fd9d632d7230d14ca1263f1e85a4"
SOURCE_FILES = {
    "Dockerfile": "Dockerfile.parallel-batch",
    "simulate-batch.parallel": "simulate-batch.parallel",
    "patch_parallel_batch.py": "patch_parallel_batch.py",
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def put_immutable(path: Path, value: bytes) -> str:
    if path.exists():
        if path.read_bytes() != value:
            raise RuntimeError(f"Immutable context changed: {path}")
    else:
        path.write_bytes(value)
    return sha256_bytes(value)


def main() -> None:
    base = inspect(BASE_TAG)
    if base["Id"] != BASE_ID:
        raise RuntimeError(f"Published parent image changed: {base['Id']}")
    source = (SOURCE / "simulate_batch_parallel.py").read_bytes()
    if sha256_bytes(source) != DIAGNOSTIC_SOURCE_SHA:
        raise RuntimeError("Diagnostic source changed after worker selection")
    old_definition = b"workers: int = 2,"
    old_parser = b'ap.add_argument("--workers", type=int, choices=(1, 2, 4), default=2)'
    if source.count(old_definition) != 1 or source.count(old_parser) != 1:
        raise RuntimeError("Expected exactly two diagnostic default-worker occurrences")
    selected = source.replace(old_definition, b"workers: int = 4,").replace(
        old_parser,
        b'ap.add_argument("--workers", type=int, choices=(1, 2, 4), default=4)',
    )
    CONTEXT.mkdir(parents=True, exist_ok=True)
    hashes = {"simulate_batch_parallel.py": put_immutable(CONTEXT / "simulate_batch_parallel.py", selected)}
    for name, source_name in SOURCE_FILES.items():
        hashes[name] = put_immutable(CONTEXT / name, (SOURCE / source_name).read_bytes())
    extras = {path.name for path in CONTEXT.iterdir()} - set(hashes)
    if extras:
        raise RuntimeError(f"Unexpected context files: {sorted(extras)}")
    log_path = EVIDENCE / "t3-parallel-batch4-build-20260927.log"
    with log_path.open("w", encoding="utf-8") as log:
        subprocess.run(
            ["docker", "build", "--network", "none", "--pull=false", "-t", TAG, str(CONTEXT)],
            stdout=log, stderr=subprocess.STDOUT, check=True,
        )
    candidate = inspect(TAG)
    base_layers = base["RootFS"]["Layers"]
    if candidate["RootFS"]["Layers"][:len(base_layers)] != base_layers:
        raise RuntimeError("Selected image does not retain exact parent layers")
    if candidate["Config"] != base["Config"]:
        raise RuntimeError("Selected image changed runtime configuration")
    installed = subprocess.check_output(
        ["docker", "run", "--rm", "--network", "none", "--read-only",
         "--entrypoint", "python", candidate["Id"], "-c",
         "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('/opt/abides_fork/simulate_batch.py').read_bytes()).hexdigest())"],
        text=True,
    ).strip()
    if installed != hashes["simulate_batch_parallel.py"]:
        raise RuntimeError("Installed selected adapter differs from generated source")
    record = {
        "parent_image": BASE_ID,
        "diagnostic_source_sha256": DIAGNOSTIC_SOURCE_SHA,
        "selected_image": candidate["Id"],
        "local_tag": TAG,
        "source_hashes": hashes,
        "installed_adapter_sha256": installed,
        "only_code_delta": "workers function default 2->4 and CLI parser default 2->4",
        "parent_layers_preserved": True,
        "runtime_config_identical": True,
        "published": False,
        "submitted": False,
    }
    path = EVIDENCE / "t3-parallel-batch4-build-20260927.json"
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"image": candidate["Id"], "record": str(path)}))


if __name__ == "__main__":
    main()
