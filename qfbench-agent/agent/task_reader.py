import json
import math
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

SEALED = {"checks", "reference", "reference_data", "solution", ".git", ".venv", "__pycache__"}


@dataclass
class Task:
    root: Path
    instruction: str
    files: list[str]
    timeout: float
    canary: str

    def redact(self, text):
        return text.replace(self.canary, "[REDACTED]") if self.canary else text


def read_task(task_dir: Path):
    root = task_dir.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("task_dir must be a directory")
    files = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SEALED)
        for name in dirs + sorted(names):
            if (Path(directory) / name).is_symlink():
                raise ValueError("Task symlinks are unsupported")
        for name in sorted(names):
            if name not in {"manifest.json", "reward.json", "pytest_report.json"}:
                files.append((Path(directory) / name).relative_to(root).as_posix())
                if len(files) > 10000:
                    raise ValueError("Too many input files")
    instruction_path, card_path = root / "instruction.md", root / "card.toml"
    if instruction_path.stat().st_size > 256000 or card_path.stat().st_size > 256000:
        raise ValueError("Instruction or card exceeds supported size")
    instruction = instruction_path.read_text(encoding="utf-8")
    card = tomllib.loads(card_path.read_text(encoding="utf-8"))
    timeout = card.get("agent", {}).get("timeout_sec")
    if type(timeout) not in (float, int) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("card.toml must provide a positive [agent].timeout_sec")
    canary = card.get("contamination", {}).get("canary_guid", "")
    if not isinstance(canary, str):
        raise ValueError("Invalid card canary metadata")
    if not instruction.strip():
        raise ValueError("instruction.md is empty")
    return Task(root, instruction, sorted(files), float(timeout), canary)


def _parquet_preview(path, limit):
    """Schema, row count and a few rows, so the model sees columns it cannot otherwise read."""
    try:
        import pyarrow.parquet as pq
        meta = pq.ParquetFile(path)
        head = meta.read_row_group(0).slice(0, 3).to_pylist() if meta.num_row_groups else []
        text = json.dumps({"schema": [f"{f.name}: {f.type}" for f in meta.schema_arrow],
                           "num_rows": meta.metadata.num_rows, "head": head},
                          ensure_ascii=False, default=str)
    except Exception as exc:  # preview is best effort; the program still inspects the file
        text = f"unreadable parquet preview: {type(exc).__name__}"
    return text[:limit]


def describe_files(task):
    descriptions, remaining = [], 20000
    for name in task.files[:1000]:
        path = task.root / name
        item = {"path": name, "bytes": path.stat().st_size}
        suffix = path.suffix.lower()
        if name not in ("instruction.md", "card.toml") and remaining > 0:
            preview = None
            if suffix in {".csv", ".json", ".txt", ".md", ".toml", ".tsv", ".yaml", ".yml"}:
                with path.open("rb") as handle:
                    preview = handle.read(min(2000, remaining)).decode("utf-8", errors="replace")
            elif suffix in {".parquet", ".pq"}:
                preview = _parquet_preview(path, min(2000, remaining))
            if preview is not None:
                item["preview"] = task.redact(preview)
                remaining -= len(preview.encode("utf-8"))
        descriptions.append(item)
    return descriptions
