"""Print a compact offline inventory of the running Track 3 image."""

import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


packages = []
for dist in sorted(metadata.distributions(), key=lambda item: item.metadata["Name"].lower()):
    license_text = dist.metadata.get("License") or ""
    license_files = [str(file) for file in (dist.files or [])
                     if "license" in str(file).lower() or "notice" in str(file).lower()]
    packages.append({
        "name": dist.metadata["Name"],
        "version": dist.version,
        "license_expression": dist.metadata.get("License-Expression"),
        "license_first_line": license_text.splitlines()[0] if license_text else None,
        "license_files": license_files,
    })

participant_files = []
for root in (Path("/opt/abides_fork"), Path("/opt/participant_build")):
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            participant_files.append({"path": str(path), "sha256": sha256(path)})

installed_abides_sources = []
for module in ("abides_core", "abides_markets"):
    for site in (Path("/usr/local/lib/python3.11/site-packages") / module,):
        if site.exists():
            for path in sorted(site.rglob("*.py")):
                installed_abides_sources.append({"path": str(path), "sha256": sha256(path)})

print(json.dumps({"packages": packages, "participant_files": participant_files,
                  "installed_abides_sources": installed_abides_sources}, indent=2))
