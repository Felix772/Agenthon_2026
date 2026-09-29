"""Verify a public Docker Hub submission image without Docker credentials.

Usage (after pushing an image)::

    python project-evidence/verify_public_dockerhub_registry.py \
      --repository felix772/agenthon-2026-t2 \
      --digest sha256:<64 lowercase hex digits> \
      --report project-evidence/t2-candidate-anonymous-pull.json

An OCI index (or Docker manifest list) and its selected linux/amd64 child
manifest are checked against their immutable SHA-256 digests. A direct
single-platform manifest is also accepted. The image config is downloaded in
full and checked against its digest, size, platform and required QFBench
interface label. Each layer is opened anonymously and one byte is read; layer
hashes are *not* verified by this small reachability check. No local Docker
configuration or registry credential is used.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


INDEX_TYPES = frozenset(
    {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)
IMAGE_TYPES = frozenset(
    {
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    }
)
ACCEPT = ", ".join(sorted(INDEX_TYPES | IMAGE_TYPES))
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
REPOSITORY_PATTERN = re.compile(
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*/[a-z0-9]+(?:[._-][a-z0-9]+)*\Z"
)
MAX_JSON_BYTES = 32 * 1024 * 1024


def _digest(value: str) -> str:
    if not DIGEST_PATTERN.fullmatch(value):
        raise ValueError(f"expected an immutable lowercase SHA-256 digest, got {value!r}")
    return value


def _descriptor(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} descriptor is missing")
    _digest(value.get("digest", ""))
    if not isinstance(value.get("size"), int) or value["size"] <= 0:
        raise RuntimeError(f"{label} descriptor has an invalid size")
    return value


def _request(
    url: str,
    token: str | None = None,
    *,
    accept: str | None = None,
    range_header: str | None = None,
):
    headers = {"User-Agent": "Agenthon2026-anonymous-release-check/2"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if accept:
        headers["Accept"] = accept
    if range_header:
        headers["Range"] = range_header
    return urlopen(Request(url, headers=headers), timeout=45)


def _read_limited(response, label: str) -> bytes:
    data = response.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise RuntimeError(f"{label} exceeds {MAX_JSON_BYTES} bytes")
    return data


def _check_hash(data: bytes, expected: str, label: str) -> None:
    actual = "sha256:" + hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise RuntimeError(f"{label} digest mismatch: expected {expected}, got {actual}")


def _fetch_json_blob(
    registry_url: str,
    token: str,
    digest: str,
    *,
    kind: str,
    expected_size: int | None = None,
) -> tuple[dict, str, int]:
    suffix = "manifests" if kind == "manifest" else "blobs"
    with _request(
        f"{registry_url}/{suffix}/{digest}",
        token,
        accept=ACCEPT if kind == "manifest" else None,
    ) as response:
        if response.status != 200:
            raise RuntimeError(f"{kind} {digest} returned HTTP {response.status}")
        data = _read_limited(response, kind)
        media_type = response.headers.get("Content-Type", "").split(";", 1)[0]
        header_digest = response.headers.get("Docker-Content-Digest")
    _check_hash(data, digest, kind)
    if expected_size is not None and len(data) != expected_size:
        raise RuntimeError(
            f"{kind} {digest} size mismatch: expected {expected_size}, got {len(data)}"
        )
    if header_digest and header_digest != digest:
        raise RuntimeError(f"{kind} {digest} returned a different Docker-Content-Digest")
    parsed = json.loads(data)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{kind} {digest} is not a JSON object")
    return parsed, media_type, len(data)


def _check_media_type(body: dict, response_type: str, allowed: frozenset[str], label: str) -> str:
    declared = body.get("mediaType")
    if declared not in allowed:
        raise RuntimeError(f"{label} has unsupported mediaType {declared!r}")
    if response_type and response_type != declared:
        raise RuntimeError(
            f"{label} response Content-Type {response_type!r} disagrees with {declared!r}"
        )
    return declared


def _anonymous_token(repository: str) -> str:
    query = urlencode({"service": "registry.docker.io", "scope": f"repository:{repository}:pull"})
    with _request(f"https://auth.docker.io/token?{query}") as response:
        if response.status != 200:
            raise RuntimeError(f"anonymous token request returned HTTP {response.status}")
        body = json.loads(_read_limited(response, "anonymous token response"))
    token = body.get("token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Docker Hub did not grant an anonymous pull-scope token")
    return token


def verify(repository: str, reference_digest: str) -> dict:
    """Return a verification report, raising on any failed check."""
    if not REPOSITORY_PATTERN.fullmatch(repository):
        raise ValueError("repository must be a lowercase Docker Hub namespace/name")
    _digest(reference_digest)
    registry_url = f"https://registry-1.docker.io/v2/{repository}"
    token = _anonymous_token(repository)

    root, root_response_type, root_bytes = _fetch_json_blob(
        registry_url, token, reference_digest, kind="manifest"
    )
    root_type = root.get("mediaType")
    if root_type in INDEX_TYPES:
        index_type = _check_media_type(root, root_response_type, INDEX_TYPES, "index")
        manifests = root.get("manifests")
        if not isinstance(manifests, list):
            raise RuntimeError("index has no manifest list")
        matches = [
            item
            for item in manifests
            if isinstance(item, dict)
            and isinstance(item.get("platform"), dict)
            and item["platform"].get("os") == "linux"
            and item["platform"].get("architecture") == "amd64"
        ]
        if len(matches) != 1:
            raise RuntimeError(f"expected one linux/amd64 child manifest, found {len(matches)}")
        child = _descriptor(matches[0], "linux/amd64 manifest")
        if child.get("mediaType") not in IMAGE_TYPES:
            raise RuntimeError("linux/amd64 child descriptor has unsupported mediaType")
        child_digest = child["digest"]
        image, image_response_type, image_bytes = _fetch_json_blob(
            registry_url,
            token,
            child_digest,
            kind="manifest",
            expected_size=child["size"],
        )
        image_type = _check_media_type(image, image_response_type, IMAGE_TYPES, "child manifest")
        if image_type != child["mediaType"]:
            raise RuntimeError("child descriptor mediaType disagrees with child manifest")
        reference_kind = "index"
        index_digest = reference_digest
        index_bytes = root_bytes
    elif root_type in IMAGE_TYPES:
        image_type = _check_media_type(root, root_response_type, IMAGE_TYPES, "image manifest")
        image = root
        child_digest = reference_digest
        image_bytes = root_bytes
        reference_kind = "single_manifest"
        index_digest = None
        index_type = None
        index_bytes = None
    else:
        raise RuntimeError(f"reference has unsupported mediaType {root_type!r}")

    config_descriptor = _descriptor(image.get("config"), "config")
    config, _, config_bytes = _fetch_json_blob(
        registry_url,
        token,
        config_descriptor["digest"],
        kind="config",
        expected_size=config_descriptor["size"],
    )
    if config.get("os") != "linux" or config.get("architecture") != "amd64":
        raise RuntimeError(
            "image config is not linux/amd64: "
            f"{config.get('os')}/{config.get('architecture')}"
        )
    labels = config.get("config") or {}
    if not isinstance(labels, dict):
        raise RuntimeError("child config.config is not an object")
    labels = labels.get("Labels") or {}
    if not isinstance(labels, dict) or labels.get("qfbench2.interface_version") != "2.0":
        raise RuntimeError("child config lacks qfbench2.interface_version=2.0")

    layers = image.get("layers")
    if not isinstance(layers, list) or not layers:
        raise RuntimeError("child manifest has no layers")
    layer_checks = []
    for number, item in enumerate(layers, 1):
        layer = _descriptor(item, f"layer {number}")
        layer_digest = layer["digest"]
        with _request(
            f"{registry_url}/blobs/{layer_digest}",
            token,
            range_header="bytes=0-0",
        ) as response:
            status = response.status
            first_byte = response.read(1)
        if status not in (200, 206) or len(first_byte) != 1:
            raise RuntimeError(
                f"anonymous layer read failed for layer {number} {layer_digest}: "
                f"HTTP {status}, {len(first_byte)} byte(s)"
            )
        layer_checks.append(
            {"number": number, "digest": layer_digest, "size": layer["size"], "http_status": status}
        )

    return {
        "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "auth": "anonymous Docker Hub pull-scope token; no workstation registry credentials",
        "registry": "docker.io",
        "repository": repository,
        "reference_digest": reference_digest,
        "reference_kind": reference_kind,
        "reference_media_type": root_type,
        "index_digest": index_digest,
        "index_media_type": index_type,
        "index_bytes": index_bytes,
        "linux_amd64_manifest_digest": child_digest,
        "linux_amd64_manifest_media_type": image_type,
        "linux_amd64_manifest_bytes": image_bytes,
        "config_digest": config_descriptor["digest"],
        "config_bytes": config_bytes,
        "config_platform": "linux/amd64",
        "interface_version": "2.0",
        "manifest_and_config_sha256_verified": True,
        "layer_access_scope": "each layer opened anonymously and first byte read; layer hashes not checked",
        "layer_checks": layer_checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="Docker Hub namespace/name")
    parser.add_argument(
        "--digest",
        "--index-digest",
        dest="digest",
        required=True,
        help="immutable OCI index or image manifest sha256:<64 lowercase hex>",
    )
    parser.add_argument("--report", required=True, type=Path, help="JSON report path")
    args = parser.parse_args()
    report = verify(args.repository, args.digest)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        "anonymous linux/amd64 image reference and config verified; "
        f"{len(report['layer_checks'])} layers readable; {args.report}"
    )


if __name__ == "__main__":
    main()
