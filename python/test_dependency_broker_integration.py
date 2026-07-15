"""Opt-in real-Docker integration for the funded dependency boundary."""

from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path

from signalos_lib.product import dependency_broker as broker


_GATE = "SIGNALOS_RUN_DEPENDENCY_PROXY_INTEGRATION"
_LABEL_FILTERS = (
    "label=signalos.owner=dependency-broker",
    "label=signalos.scope=funded-dependency",
)
_SENSITIVE_ENV_RE = re.compile(
    r"(?:^|_)(?:API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|SECRET(?:_?KEY)?|TOKEN|"
    r"PASSWORD|PASSWD|CREDENTIALS?|AUTHORIZATION)(?:_|$)",
    re.IGNORECASE,
)


@contextlib.contextmanager
def _without_provider_credentials() -> Iterator[None]:
    saved = {
        name: os.environ.pop(name)
        for name in tuple(os.environ)
        if _SENSITIVE_ENV_RE.search(name)
    }
    try:
        yield
    finally:
        os.environ.update(saved)


@unittest.skipUnless(
    os.environ.get(_GATE) == "1",
    f"set {_GATE}=1 to run the real Docker dependency integration",
)
class DependencyBrokerDockerIntegrationTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        if os.name != "posix":
            self.fail("the mandatory real-Docker integration requires a Linux host")
        self.docker = shutil.which("docker")
        if not self.docker:
            self.fail("Docker CLI is required after the integration gate is enabled")
        self.docker_env = {
            key: value
            for key, value in os.environ.items()
            if not _SENSITIVE_ENV_RE.search(key)
            and key
            not in {
                "DOCKER_CONFIG",
                "DOCKER_CONTEXT",
                "DOCKER_HOST",
                "DOCKER_TLS_VERIFY",
                "DOCKER_CERT_PATH",
            }
        }
        raw_endpoint = self._docker(
            "context",
            "inspect",
            "--format",
            "{{json .Endpoints.docker.Host}}",
            bound=False,
        )
        try:
            endpoint = json.loads(raw_endpoint.strip())
        except json.JSONDecodeError as exc:
            self.fail(f"Docker context returned malformed endpoint JSON: {exc}")
        if endpoint not in broker.UNIX_DOCKER_ENGINE_ENDPOINTS:
            self.fail("Docker context is not an approved local Linux engine endpoint")
        self.endpoint = endpoint
        daemon_os = json.loads(
            self._docker("info", "--format", "{{json .OSType}}").strip()
        )
        self.assertEqual(daemon_os, "linux")

        self.repo = Path(__file__).resolve().parents[1]
        self.policy_path = (
            self.repo / "scripts" / "backend_matrix" / "dependencies" / "policy.json"
        )
        self.policy = broker.load_dependency_policy(self.policy_path)
        self._docker("image", "inspect", self.policy.image)
        self.before = self._resource_snapshot()
        self.assertEqual(
            self.before,
            (frozenset(), frozenset()),
            "the dedicated integration daemon has stale dependency-broker resources",
        )

    def tearDown(self) -> None:
        if not hasattr(self, "before"):
            return
        after = self._resource_snapshot()
        self.assertEqual(
            after,
            self.before,
            f"dependency-broker Docker residue detected: before={self.before}, after={after}",
        )

    def _docker(self, *args: str, bound: bool = True) -> str:
        command = [str(self.docker)]
        if bound:
            command.extend(("--host", self.endpoint))
        command.extend(args)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                env=dict(self.docker_env),
                shell=False,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.fail(f"Docker control call failed: {type(exc).__name__}: {exc}")
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "Docker failed")[-2000:]
            self.fail(f"Docker control call returned {completed.returncode}: {detail}")
        return completed.stdout or ""

    def _resource_snapshot(self) -> tuple[frozenset[str], frozenset[str]]:
        filters = [part for item in _LABEL_FILTERS for part in ("--filter", item)]
        containers = self._ids(self._docker("ps", "-aq", *filters))
        networks = self._ids(self._docker("network", "ls", "-q", *filters))
        return containers, networks

    def _ids(self, output: str) -> frozenset[str]:
        values = frozenset(line.strip().lower() for line in output.splitlines() if line.strip())
        if any(re.fullmatch(r"[0-9a-f]{12,64}", value) is None for value in values):
            self.fail("Docker returned a malformed resource identifier")
        return values

    def _assert_key_absent(self, root: Path, key: bytes) -> None:
        needles = (key, key.hex().encode("ascii"))
        for path in root.rglob("*"):
            if path.is_symlink():
                self.fail(f"integration bundle contains an unexpected symlink: {path.name}")
            if not path.is_file():
                continue
            with path.open("rb") as handle:
                carry = b""
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    sample = carry + chunk
                    if any(needle in sample for needle in needles):
                        self.fail(f"attestation key leaked into integration artifact: {path.name}")
                    carry = sample[-63:]

    def test_public_broker_prepares_verifies_and_cleans_real_bundle(self) -> None:
        key_owner = bytearray(secrets.token_bytes(32))
        key_bytes = bytes(key_owner)
        try:
            with tempfile.TemporaryDirectory(
                prefix="signalos-dependency-proxy-it-"
            ) as temporary:
                root = Path(temporary).resolve()
                bundle = root / "bundle"
                try:
                    with _without_provider_credentials():
                        receipt = broker.prepare_dependency_bundle(
                            self.policy_path,
                            bundle,
                            engine="docker",
                            timeout=600,
                            attestation_key=key_bytes,
                        )
                    verified = broker.verify_dependency_bundle(
                        self.policy_path,
                        bundle,
                        attestation_key=key_bytes,
                    )
                    self.assertEqual(verified, receipt)
                    self.assertEqual(receipt["schema"], broker.RECEIPT_SCHEMA)
                    self.assertEqual(receipt["status"], "ready")
                    self.assertEqual(receipt["profile"], self.policy.profile)
                    self.assertEqual(receipt["image"], self.policy.image)

                    provisioner = receipt["provisioner"]
                    self.assertEqual(provisioner["schema"], broker.RUNNER_EVIDENCE_SCHEMA)
                    self.assertEqual(provisioner["engine"], "docker")
                    self.assertEqual(
                        provisioner["host_trust_profile"],
                        broker.TRUSTED_LOCAL_DOCKER_ENGINE_PROFILE,
                    )
                    self.assertEqual(provisioner["docker_endpoint"], self.endpoint)
                    self.assertEqual(provisioner["daemon_os_type"], "linux")
                    self.assertEqual(provisioner["platform"], self.policy.platform)
                    self.assertEqual(provisioner["installer_image"], self.policy.image)
                    self.assertEqual(provisioner["proxy_image"], self.policy.proxy_image)
                    self.assertEqual(provisioner["pull_policy"], "never")
                    self.assertEqual(provisioner["cleanup_verified"], True)
                    self.assertEqual(
                        provisioner["allowed_connect_authorities"],
                        list(self.policy.proxy_allowed_connect_authorities),
                    )
                    self.assertEqual(
                        provisioner["installer_network"],
                        self.policy.proxy_installer_network,
                    )
                    self.assertEqual(
                        provisioner["proxy_egress_network"],
                        self.policy.proxy_egress_network,
                    )
                    self.assertEqual(provisioner["tls_mode"], self.policy.proxy_tls_mode)

                    self.assertEqual(
                        receipt["fetch"],
                        {
                            "scripts_ignored": True,
                            "audit": False,
                            "fund": False,
                            "lockfile_allowed_registry_origins": list(
                                self.policy.allowed_origins
                            ),
                            "egress_policy": broker.TRUSTED_EGRESS_POLICY,
                        },
                    )
                    self.assertTrue((bundle / "package.json").is_file())
                    self.assertTrue((bundle / "package-lock.json").is_file())
                    self.assertTrue((bundle / broker.ARCHIVE_NAME).is_file())
                    self.assertTrue(
                        (bundle / ".signalos-dependency-receipt.json").is_file()
                    )
                    self.assertFalse((bundle / "node_modules").exists())
                finally:
                    self._assert_key_absent(root, key_bytes)
        finally:
            key_bytes = b""
            for index in range(len(key_owner)):
                key_owner[index] = 0

    def test_public_broker_failure_removes_runtime_and_staging(self) -> None:
        key_owner = bytearray(secrets.token_bytes(32))
        key_bytes = bytes(key_owner)
        try:
            with tempfile.TemporaryDirectory(
                prefix="signalos-dependency-proxy-failure-it-"
            ) as temporary:
                root = Path(temporary).resolve()
                policy_root = root / "dependencies"
                shutil.copytree(self.policy_path.parent, policy_root)
                failing_policy = policy_root / "policy.json"
                failing_lock = policy_root / self.policy.profile / "package-lock.json"
                lock = json.loads(failing_lock.read_text(encoding="utf-8"))
                react = lock.get("packages", {}).get("node_modules/react")
                self.assertIsInstance(react, dict)
                self.assertRegex(str(react.get("integrity") or ""), r"^sha512-")
                react["integrity"] = "sha512-" + base64.b64encode(
                    b"\x00" * 64
                ).decode("ascii")
                failing_lock.write_text(
                    json.dumps(lock, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                bundle = root / "failed-bundle"
                try:
                    with _without_provider_credentials():
                        with self.assertRaises(broker.DependencyBrokerError):
                            broker.prepare_dependency_bundle(
                                failing_policy,
                                bundle,
                                engine="docker",
                                timeout=600,
                                attestation_key=key_bytes,
                            )
                    self.assertFalse(bundle.exists())
                    self.assertEqual(
                        list(root.glob(".failed-bundle.staging-*")),
                        [],
                        "failed provisioning left a private staging directory",
                    )
                    self.assertEqual(
                        self._resource_snapshot(),
                        self.before,
                        "failed provisioning left Docker resources before tearDown",
                    )
                finally:
                    self._assert_key_absent(root, key_bytes)
        finally:
            key_bytes = b""
            for index in range(len(key_owner)):
                key_owner[index] = 0


if __name__ == "__main__":
    unittest.main()
