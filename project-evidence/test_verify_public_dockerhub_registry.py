"""Offline registry fixtures for the anonymous release verifier."""

from __future__ import annotations

import hashlib
import io
import json
import unittest
from unittest.mock import patch

import verify_public_dockerhub_registry as verifier


def encoded(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def descriptor(data: bytes, media_type: str) -> dict:
    return {
        "mediaType": media_type,
        "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }


class Response(io.BytesIO):
    def __init__(self, data: bytes, content_type: str = "application/json") -> None:
        super().__init__(data)
        self.status = 200
        self.headers = {"Content-Type": content_type}


def fixture(*, architecture: str = "amd64", single_manifest: bool = False):
    repository = "felix772/agenthon-2026-t2"
    layer = b"synthetic layer bytes"
    layer_descriptor = descriptor(layer, "application/vnd.oci.image.layer.v1.tar+gzip")
    config = encoded(
        {
            "os": "linux",
            "architecture": architecture,
            "config": {"Labels": {"qfbench2.interface_version": "2.0"}},
        }
    )
    config_descriptor = descriptor(config, "application/vnd.oci.image.config.v1+json")
    image = encoded(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": config_descriptor,
            "layers": [layer_descriptor],
        }
    )
    image_descriptor = descriptor(image, "application/vnd.oci.image.manifest.v1+json")
    image_descriptor["platform"] = {"os": "linux", "architecture": "amd64"}
    index = encoded(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [image_descriptor],
        }
    )
    index_descriptor = descriptor(index, "application/vnd.oci.image.index.v1+json")
    base = f"https://registry-1.docker.io/v2/{repository}"
    resources = {
        f"{base}/manifests/{index_descriptor['digest']}": (index, index_descriptor["mediaType"]),
        f"{base}/manifests/{image_descriptor['digest']}": (image, image_descriptor["mediaType"]),
        f"{base}/blobs/{config_descriptor['digest']}": (config, "application/octet-stream"),
        f"{base}/blobs/{layer_descriptor['digest']}": (layer, "application/octet-stream"),
    }
    reference_digest = image_descriptor["digest"] if single_manifest else index_descriptor["digest"]
    return repository, reference_digest, resources


class RegistryVerifierTests(unittest.TestCase):
    def run_fixture(
        self,
        architecture: str = "amd64",
        corrupt_image: bool = False,
        single_manifest: bool = False,
    ) -> dict:
        repository, reference_digest, resources = fixture(
            architecture=architecture, single_manifest=single_manifest
        )
        if corrupt_image:
            image_url = next(
                url
                for url, (data, _) in resources.items()
                if "/manifests/" in url and b'"config"' in data
            )
            _, media_type = resources[image_url]
            resources[image_url] = (b"{}", media_type)
        calls = []

        def fake_request(url, token=None, *, accept=None, range_header=None):
            calls.append((url, token, range_header))
            if url.startswith("https://auth.docker.io/token?"):
                self.assertIsNone(token)
                self.assertIn(f"repository%3A{repository.replace('/', '%2F')}%3Apull", url)
                return Response(b'{"token":"synthetic-anonymous-token"}')
            self.assertEqual(token, "synthetic-anonymous-token")
            data, media_type = resources[url]
            return Response(data, media_type)

        with patch.object(verifier, "_request", side_effect=fake_request):
            result = verifier.verify(repository, reference_digest)
        self.assertEqual(len([call for call in calls if call[2] == "bytes=0-0"]), 1)
        return result

    def test_complete_public_image(self) -> None:
        report = self.run_fixture()
        self.assertTrue(report["manifest_and_config_sha256_verified"])
        self.assertEqual(report["config_platform"], "linux/amd64")
        self.assertEqual(report["interface_version"], "2.0")
        self.assertEqual(len(report["layer_checks"]), 1)
        self.assertEqual(report["reference_kind"], "index")

    def test_direct_single_platform_manifest(self) -> None:
        report = self.run_fixture(single_manifest=True)
        self.assertEqual(report["reference_kind"], "single_manifest")
        self.assertIsNone(report["index_digest"])
        self.assertEqual(report["reference_digest"], report["linux_amd64_manifest_digest"])

    def test_index_and_config_platform_must_agree(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "config is not linux/amd64"):
            self.run_fixture(architecture="arm64")

    def test_child_manifest_bytes_must_match_digest(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "digest mismatch"):
            self.run_fixture(corrupt_image=True)


if __name__ == "__main__":
    unittest.main()
