import json
import shutil
from pathlib import Path, PurePosixPath

RESERVED = {"reward.json", "pytest_report.json", "reward.txt", "reward", "verifier", "checks", ".agent"}


def deliverable_name(name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise ValueError("Deliverables must be relative POSIX paths")
    path = PurePosixPath(name)
    if path.is_absolute() or any(p in ("..", ".", "") for p in name.split("/")):
        raise ValueError("Deliverable path escapes the output directory")
    if any(p.lower() in RESERVED or p.lower().startswith("reward.") for p in path.parts):
        raise ValueError("Agent cannot produce reward, verifier or internal artifacts")
    return path


def prepare_output(task_dir: Path, out: Path):
    out = out.resolve()
    if out.is_relative_to(task_dir) or task_dir.is_relative_to(out):
        raise ValueError("Input and output directories must not overlap")
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError("Use a fresh empty output directory for every solve")
    (out / ".agent").mkdir()
    return out


def write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def validate_outputs(directory, names):
    if not isinstance(names, list) or not names or len(names) > 100:
        raise ValueError("Solution must declare 1–100 deliverables")
    normalized = [str(deliverable_name(name)) for name in names]
    if len(set(normalized)) != len(normalized):
        raise ValueError("Duplicate deliverable paths")
    for path in directory.rglob("*"):
        if path.is_symlink() or (path.is_file() and path.stat().st_nlink > 1):
            raise ValueError("Output symlinks/hardlinks are forbidden")
        deliverable_name(path.relative_to(directory).as_posix())
    for name in normalized:
        path = directory / name
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Missing or empty declared deliverable: {name}")
        if path.suffix.lower() == ".json":
            with path.open(encoding="utf-8") as handle:
                json.load(handle)
        elif path.suffix.lower() == ".parquet":
            import pyarrow.parquet as pq
            pq.read_metadata(path)
        elif path.suffix.lower() == ".py":
            compile(path.read_text(encoding="utf-8"), name, "exec")
    return normalized


def publish_outputs(directory, names, out):
    for name in validate_outputs(directory, names):
        destination = out / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(directory / name, destination)
