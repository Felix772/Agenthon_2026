import json
import csv
import math
import os
import stat
from pathlib import Path, PurePosixPath

RESERVED = {"reward.json", "pytest_report.json", "reward.txt", "reward", "verifier", "checks", ".agent"}
OUTPUT_LIMIT = 64 * 1024 * 1024
REPORT_RESERVE = 1024 * 1024
_output_acceptance_fd = None


def finalize_output_permissions(out):
    """Make the complete finished output tree readable by the external checker."""
    if os.name != "posix":
        # Production execution is Linux; Windows mode emulation cannot prove
        # cross-UID readability and must not change local CLI behavior.
        return
    out = Path(out)
    if not out.exists() and not out.is_symlink():
        return
    pending = [out]
    entries = []
    while pending:
        path = pending.pop()
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            entries.append((path, 0o755))
            pending.extend(path.iterdir())
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            # Preserve executable deliverables; add read access without allowing
            # group/world writes or special permission bits.
            entries.append((path, 0o644 | (stat.S_IMODE(info.st_mode) & 0o111)))
        else:
            raise ValueError("Output links and special files are forbidden")
    # Validate the entire tree before changing any mode. The supervising CLI
    # calls this after its child and generated workers have stopped.
    for path, mode in entries:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fchmod(descriptor, mode)
        finally:
            os.close(descriptor)


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
    global _output_acceptance_fd
    if out.is_symlink():
        raise ValueError("Output directory must not be a symlink")
    out = out.resolve()
    if out.is_relative_to(task_dir) or task_dir.is_relative_to(out):
        raise ValueError("Input and output directories must not overlap")
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError("Use a fresh empty output directory for every solve")
    (out / ".agent").mkdir()
    if _output_acceptance_fd is not None:
        # Send before task parsing/model work; close before generated workers
        # exist so they cannot fabricate permission to alter a different tree.
        descriptor, _output_acceptance_fd = _output_acceptance_fd, None
        try:
            info = out.lstat()
            receipt = {"path": str(out), "identity": [info.st_dev, info.st_ino]}
            os.write(descriptor, json.dumps(receipt).encode("utf-8"))
        finally:
            os.close(descriptor)
    return out


def write_json(path, payload):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def output_tree_bytes(directory, limit=OUTPUT_LIMIT):
    total = 0
    for path in directory.rglob("*"):
        if path.is_symlink() or (path.is_file() and path.stat().st_nlink > 1):
            raise ValueError("Output symlinks/hardlinks are forbidden")
        if path.is_file():
            total += path.stat().st_size
            if total > limit:
                raise ValueError("Complete output tree exceeds the 64 MiB budget including diagnostics reserve")
    return total


def _json_constant(value):
    raise ValueError("JSON contains a nonfinite numeric constant")


def _finite_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON contains a nonfinite number")
    if isinstance(value, dict):
        for item in value.values():
            _finite_json(item)
    elif isinstance(value, list):
        for item in value:
            _finite_json(item)


def _check_columns(columns, requirement):
    required = requirement.get("columns", [])
    if any(column not in columns for column in required):
        raise ValueError("Artifact is missing instruction-required columns")
    if requirement.get("exact_columns") and set(columns) != set(required):
        raise ValueError("Artifact has extra columns forbidden by instruction")
    if requirement.get("column_order") and [column for column in columns if column in required] != required:
        raise ValueError("Artifact columns do not follow the explicit instruction order")


def validate_outputs(directory, names, contract=None):
    if not isinstance(names, list) or not names or len(names) > 100:
        raise ValueError("Solution must declare 1–100 deliverables")
    normalized = [str(deliverable_name(name)) for name in names]
    if len(set(normalized)) != len(normalized):
        raise ValueError("Duplicate deliverable paths")
    requirements = {item["path"]: item for item in (contract or {}).get("required_artifacts", [])}
    if any(name not in normalized for name in requirements):
        raise ValueError("Model omitted an instruction-required deliverable: " + ", ".join(name for name in requirements if name not in normalized))
    output_tree_bytes(directory, OUTPUT_LIMIT - REPORT_RESERVE)
    for path in directory.rglob("*"):
        if path.is_symlink() or (path.is_file() and path.stat().st_nlink > 1):
            raise ValueError("Output symlinks/hardlinks are forbidden")
        deliverable_name(path.relative_to(directory).as_posix())
    for name in normalized:
        path = directory / name
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Missing or empty declared deliverable: {name}")
        requirement = requirements.get(name, {})
        if path.suffix.lower() == ".json":
            with path.open(encoding="utf-8") as handle:
                data = json.load(handle, parse_constant=_json_constant)
            _finite_json(data)
            keys = requirement.get("keys", [])
            if keys and (not isinstance(data, dict) or any(key not in data for key in keys)):
                raise ValueError("Artifact is missing instruction-required top-level JSON keys")
            if requirement.get("exact_keys") and set(data) != set(keys):
                raise ValueError("Artifact has extra top-level JSON keys forbidden by instruction")
        elif path.suffix.lower() in {".csv", ".tsv"}:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle, delimiter="\t" if path.suffix.lower() == ".tsv" else ",", strict=True)
                columns = next(reader, [])
                _check_columns(columns, requirement)
                for row in reader:
                    if not row:
                        continue
                    if len(row) != len(columns):
                        raise ValueError("CSV contains a row with a different field count")
                    if requirement.get("finite"):
                        for cell in row:
                            try:
                                number = float(cell)
                            except ValueError:
                                continue
                            if not math.isfinite(number):
                                raise ValueError("Artifact violates instruction-required finite numeric values")
        elif path.suffix.lower() == ".parquet":
            import pyarrow.parquet as pq
            metadata = pq.read_metadata(path)
            _check_columns(metadata.schema.names, requirement)
        elif path.suffix.lower() == ".py":
            compile(path.read_text(encoding="utf-8"), name, "exec")
    return normalized


def publish_outputs(directory, names, out, contract=None):
    validated = validate_outputs(directory, names, contract)
    output_tree_bytes(out, OUTPUT_LIMIT - REPORT_RESERVE)
    for name in validated:
        destination = out / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Move on the same output filesystem; copying doubles peak output size.
        os.replace(directory / name, destination)
