import math
import csv
import io
import json
import os
import struct
import time
import tomllib
import zipfile
import xml.etree.ElementTree as ET
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


def _sample_text(raw, suffix, complete):
    text = raw.decode("utf-8-sig", errors="replace")
    if suffix in {".csv", ".tsv"}:
        # Do not interpret an incomplete trailing record as a complete row.
        if not complete:
            text = text.rsplit("\n", 1)[0]
        rows = list(csv.reader(io.StringIO(text), delimiter="\t" if suffix == ".tsv" else ",", strict=True))[:21]
        if not rows:
            return {"format": "delimited", "columns": [], "sample_rows": []}
        columns = rows[0][:64]
        sample = rows[1:6]
        numeric = []
        for i, column in enumerate(columns):
            cells = [row[i] for row in rows[1:] if i < len(row) and row[i].strip()]
            try:
                numbers = [float(cell) for cell in cells]
            except ValueError:
                continue
            if numbers:
                finite = [value for value in numbers if math.isfinite(value)]
                numeric.append({"column": column, "observed": len(numbers), "nonfinite": len(numbers) - len(finite),
                                "min": min(finite) if finite else None, "max": max(finite) if finite else None})
        return {"format": "delimited", "columns": columns, "sample_rows": [row[:64] for row in sample],
                "sample_numeric": numeric, "stats_scope": "first 20 parsed rows; not full-file statistics",
                "prefix_complete": complete, "ragged_sample": any(len(row) != len(rows[0]) for row in rows[1:])}
    if suffix == ".json":
        if not complete:
            return {"format": "json", "inspection": "file exceeds bounded JSON parser", "prefix": text[:1000]}
        data = json.loads(text)
        def shape(value, depth=0):
            if depth >= 3:
                return type(value).__name__
            if isinstance(value, dict):
                return {str(k)[:100]: shape(v, depth + 1) for k, v in list(value.items())[:32]}
            if isinstance(value, list):
                return {"type": "array", "length": len(value), "first_items": [shape(v, depth + 1) for v in value[:3]]}
            if isinstance(value, str):
                return {"type": "str", "sample": value[:100]}
            if isinstance(value, float) and not math.isfinite(value):
                return "nonfinite number"
            return {"type": type(value).__name__, "sample": value}
        return {"format": "json", "shape": shape(data)}
    return {"preview": text[:2000]}


def _inspect_parquet(path):
    # Parquet metadata itself can be arbitrarily large. Bound it before loading
    # pyarrow; large datasets get schema-only inspection, no decompression.
    with path.open("rb") as handle:
        handle.seek(-8, 2)
        footer = handle.read(8)
    size = struct.unpack("<I", footer[:4])[0]
    if footer[4:] != b"PAR1" or size > 65536:
        return {"format": "parquet", "inspection": "invalid or oversized footer"}
    import pyarrow.parquet as pq
    file = pq.ParquetFile(path)
    return {"format": "parquet", "rows": file.metadata.num_rows, "row_groups": file.metadata.num_row_groups,
            "schema": str(file.schema_arrow)[:2000], "inspection": "footer/schema only; no row data decompressed"}


def _inspect_xlsx(path):
    # Spreadsheet ZIP/XML is inspected without evaluating formulas or loading
    # unbounded shared strings and worksheet contents into memory.
    if path.stat().st_size > 4 * 1024 * 1024:
        return {"format": "xlsx", "inspection": "archive exceeds 4 MiB inspection limit"}
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 512 or sum(info.file_size for info in entries) > 2 * 1024 * 1024:
            return {"format": "xlsx", "inspection": "archive exceeds expanded inspection limits"}
        def xml(name):
            info = archive.getinfo(name)
            if info.file_size > 65536:
                raise ValueError("XML member exceeds inspection limit")
            raw = archive.read(info)
            if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                raise ValueError("XML declarations unsupported")
            return ET.fromstring(raw)
        workbook = xml("xl/workbook.xml")
        sheets = [node.attrib.get("name", "")[:100] for node in workbook.iter() if node.tag.endswith("}sheet")][:16]
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ["".join(node.itertext())[:100] for node in xml("xl/sharedStrings.xml")][:128]
        samples = []
        for name in sorted(archive.namelist()):
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml") and len(samples) < 3:
                tree = xml(name)
                rows = []
                for row in [n for n in tree.iter() if n.tag.endswith("}row")][:6]:
                    cells = []
                    for cell in list(row)[:32]:
                        value = "".join(n.text or "" for n in cell.iter() if n.tag.endswith(("}v", "}t")))[:100]
                        if cell.attrib.get("t") == "s" and value.isdigit():
                            value = shared[int(value)] if int(value) < len(shared) else "[shared string beyond sample]"
                        cells.append(value)
                    rows.append(cells)
                samples.append({"member": name, "sample_rows": rows})
    return {"format": "xlsx", "sheet_names": sheets, "samples": samples, "formula_policy": "cached values only; formulas not evaluated"}


def describe_files(task):
    descriptions, remaining, read_remaining = [], 32000, 4 * 1024 * 1024
    until = time.monotonic() + 3
    # Mentioned inputs precede unrelated files without any task-identity routing.
    names = sorted(task.files, key=lambda name: (Path(name).name not in task.instruction, name))
    inspected = 0
    for name in names[:1000]:
        path = task.root / name
        item = {"path": name, "bytes": path.stat().st_size}
        suffix = path.suffix.lower()
        if name not in ("instruction.md", "card.toml") and remaining > 0 and read_remaining > 0 and inspected < 32 and time.monotonic() < until:
            try:
                if suffix in {".csv", ".tsv", ".json", ".txt", ".md", ".toml"}:
                    with path.open("rb") as handle:
                        raw = handle.read(min(65536, read_remaining))
                    read_remaining -= len(raw)
                    details = _sample_text(raw, suffix, len(raw) == item["bytes"])
                elif suffix in {".parquet", ".pqt", ".pq"}:
                    details = _inspect_parquet(path)
                    read_remaining -= 65536
                elif suffix == ".xlsx":
                    if read_remaining < 2 * 1024 * 1024:
                        details = {"format": "xlsx", "inspection": "expanded-read budget exhausted"}
                    else:
                        details = _inspect_xlsx(path)
                        read_remaining -= 2 * 1024 * 1024
                else:
                    details = {}
                encoded = task.redact(json.dumps(details, ensure_ascii=False, allow_nan=False))
                # Never truncate serialized JSON into a malformed prompt value.
                if len(encoded) <= min(6000, remaining):
                    item["inspection"] = json.loads(encoded)
                    remaining -= len(encoded)
                else:
                    item["inspection"] = {"status": "schema/sample exceeds prompt budget"}
                inspected += 1
            except (ValueError, OSError, ImportError, RuntimeError, RecursionError, csv.Error, ET.ParseError, zipfile.BadZipFile, KeyError, struct.error) as exc:
                item["inspection"] = {"status": "unavailable", "error_type": type(exc).__name__}
        descriptions.append(item)
    # Redact names and metadata as well as file contents.
    return json.loads(task.redact(json.dumps(descriptions, ensure_ascii=False, allow_nan=False)))
