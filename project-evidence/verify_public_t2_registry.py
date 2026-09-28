"""Verify the first T2 Docker Hub release without local registry credentials.

Fetch the index and linux/amd64 manifest by immutable digest, verify their
content hashes, then open the config blob and every layer as an anonymous
reader. Only one byte per blob is consumed; the report makes that scope clear.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REPOSITORY = "felix772/agenthon-2026-t2"
INDEX_DIGEST = "sha256:faa9e5cb19d4964f42dd97c4a9622f05c6507dea37656eb15eef672842bf8f6c"
TAG = "baseline-20260926"
REGISTRY = f"https://registry-1.docker.io/v2/{REPOSITORY}"
ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
REPORT = Path(__file__).with_name("t2-anonymous-registry-pull-20260926.json")


def _request(url: str, token: str | None = None, *, accept: str | None = None, range_header: str | None = None):
    headers = {"User-Agent": "Agenthon2026-anonymous-release-check/1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if accept:
        headers["Accept"] = accept
    if range_header:
        headers["Range"] = range_header
    return urlopen(Request(url, headers=headers), timeout=45)


def _manifest(token: str, digest: str) -> dict:
    with _request(f"{REGISTRY}/manifests/{digest}", token, accept=ACCEPT) as response:
        raw = response.read()
        status = response.status
        media_type = response.headers.get("Content-Type", "").split(";", 1)[0]
    actual = "sha256:" + hashlib.sha256(raw).hexdigest()
    if status != 200 or actual != digest:
        raise RuntimeError(f"manifest verification failed for {digest}: status {status}, hash {actual}")
    parsed = json.loads(raw)
    return {"digest": digest, "media_type": media_type, "byte_length": len(raw), "body": parsed}


def main() -> None:
    query = urlencode({"service": "registry.docker.io", "scope": f"repository:{REPOSITORY}:pull"})
    with _request(f"https://auth.docker.io/token?{query}") as response:
        token = json.load(response)["token"]
    index = _manifest(token, INDEX_DIGEST)
    matches = [
        item
        for item in index["body"]["manifests"]
        if item.get("platform", {}).get("os") == "linux"
        and item.get("platform", {}).get("architecture") == "amd64"
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one linux/amd64 manifest, found {len(matches)}")
    amd64 = _manifest(token, matches[0]["digest"])
    blobs = [("config", amd64["body"]["config"])] + [
        (f"layer-{number}", layer)
        for number, layer in enumerate(amd64["body"]["layers"], 1)
    ]
    checks = []
    for label, blob in blobs:
        digest = blob["digest"]
        with _request(f"{REGISTRY}/blobs/{digest}", token, range_header="bytes=0-0") as response:
            first_byte = response.read(1)
            status = response.status
        if status not in (200, 206) or len(first_byte) != 1:
            raise RuntimeError(f"anonymous blob read failed for {label} {digest}: status {status}")
        checks.append({"label": label, "digest": digest, "status": status, "bytes_read": 1})
    report = {
        "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "auth": "anonymous Docker Hub pull-scope token; no workstation credentials",
        "registry": "docker.io",
        "repository": REPOSITORY,
        "tag": TAG,
        "index_digest": INDEX_DIGEST,
        "index_media_type": index["media_type"],
        "linux_amd64_manifest_digest": amd64["digest"],
        "linux_amd64_manifest_media_type": amd64["media_type"],
        "manifest_hashes_verified": True,
        "blob_access_scope": "opened every config/layer blob and read its first byte; not a full blob hash check",
        "blob_checks": checks,
    }
    REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"anonymous index and linux/amd64 manifest verified; {len(checks)} blobs readable; {REPORT}")


if __name__ == "__main__":
    main()
