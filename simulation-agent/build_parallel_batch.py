"""Build the T3 batch candidate over the verified published runtime image.

The context includes only the adapter, source guard, and Dockerfile. This
script does not publish or submit the resulting local image.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "simulation-agent"
CONTEXT = ROOT / ".validation" / "t3-parallel-batch-context-20260927"
EVIDENCE = ROOT / "project-evidence"
BASE_TAG = "simulation-agent:runtime-20260925"
BASE_ID = "sha256:c5724d34e2a8c951e4645b7c9aed1b95e3eabf1bd3dc24c0195055700cdbd45d"
TAG = "simulation-agent:parallel-batch-20260927"
SOURCE_FILES = {
    "Dockerfile": "Dockerfile.parallel-batch",
    "simulate_batch_parallel.py": "simulate_batch_parallel.py",
    "simulate-batch.parallel": "simulate-batch.parallel",
    "patch_parallel_batch.py": "patch_parallel_batch.py",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect(image: str) -> dict:
    raw = subprocess.check_output(["docker", "image", "inspect", image], text=True)
    return json.loads(raw)[0]


def main() -> None:
    base = inspect(BASE_TAG)
    if base["Id"] != BASE_ID:
        raise RuntimeError(f"Local base image changed: {base['Id']}")
    CONTEXT.mkdir(parents=True, exist_ok=True)
    sources = {}
    for name, source_name in SOURCE_FILES.items():
        source = SOURCE / source_name
        target = CONTEXT / name
        if target.exists() and target.read_bytes() != source.read_bytes():
            raise RuntimeError(f"Existing whitelisted context differs: {target}")
        if not target.exists():
            shutil.copyfile(source, target)
        sources[name] = sha256(target)
    extra = {path.name for path in CONTEXT.iterdir()} - set(SOURCE_FILES)
    if extra:
        raise RuntimeError(f"Unexpected file in whitelisted context: {sorted(extra)}")
    log_path = EVIDENCE / "t3-parallel-batch-build-20260927.log"
    with log_path.open("w", encoding="utf-8") as log:
        subprocess.run(
            ["docker", "build", "--network", "none", "--pull=false", "-t", TAG, str(CONTEXT)],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    candidate = inspect(TAG)
    if candidate["Id"] == BASE_ID:
        raise RuntimeError("Build did not create a derived image")
    base_layers = base["RootFS"]["Layers"]
    if candidate["RootFS"]["Layers"][: len(base_layers)] != base_layers:
        raise RuntimeError("Candidate does not preserve every base layer")
    if candidate["Config"] != base["Config"]:
        raise RuntimeError("Candidate changed runtime image configuration")
    actual_source_sha = subprocess.check_output(
        [
            "docker", "run", "--rm", "--network", "none", "--read-only",
            "--entrypoint", "python", candidate["Id"], "-c",
            "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('/opt/abides_fork/simulate_batch.py').read_bytes()).hexdigest())",
        ],
        text=True,
    ).strip()
    if actual_source_sha != sources["simulate_batch_parallel.py"]:
        raise RuntimeError("Built adapter bytes differ from reviewed candidate source")
    record = {
        "base": BASE_ID,
        "candidate": candidate["Id"],
        "tag": TAG,
        "source_hashes": sources,
        "base_layers_preserved": True,
        "runtime_config_identical": True,
        "built_adapter_sha256": actual_source_sha,
        "published": False,
        "submitted": False,
    }
    out = EVIDENCE / "t3-parallel-batch-build-20260927.json"
    out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"candidate": candidate["Id"], "record": str(out)}))


if __name__ == "__main__":
    main()
