"""Build a single-architecture release image from an immutable base reference.

This creates no registry state. Publishing and anonymous-pull verification are
deliberately separate final-release actions.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess


IMMUTABLE_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")


def require_immutable_image(reference: str):
    if not IMMUTABLE_IMAGE.fullmatch(reference):
        raise ValueError("--base must be a registry/repository@sha256:<64 lowercase hex> reference")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Immutable official sandbox image reference")
    parser.add_argument("--image", required=True, help="Local tag for the newly built release image")
    parser.add_argument("--record", type=Path, required=True, help="New JSON evidence record to write")
    args = parser.parse_args()
    require_immutable_image(args.base)
    if args.record.exists():
        parser.error("--record must name a new file")
    project = Path(__file__).resolve().parents[1]
    subprocess.run([
        "docker", "build", "--platform", "linux/amd64", "--build-arg", "BASE_IMAGE=" + args.base,
        "--tag", args.image, str(project),
    ], check=True)
    image = json.loads(subprocess.check_output(["docker", "image", "inspect", args.image], text=True))[0]
    if image.get("Architecture") != "amd64" or image.get("Os") != "linux":
        raise RuntimeError("Built image is not linux/amd64")
    if image.get("Config", {}).get("Labels", {}).get("qfbench2.interface_version") != "2.0":
        raise RuntimeError("Built image is missing qfbench2.interface_version=2.0")
    record = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "base_image": args.base,
        "image_tag": args.image,
        "image_id": image["Id"],
        "platform": "linux/amd64",
        "interface_version": "2.0",
    }
    args.record.parent.mkdir(parents=True, exist_ok=True)
    args.record.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
