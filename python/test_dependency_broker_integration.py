"""Opt-in real-Docker integration for the funded dependency boundary.

Two contexts are exercised against a real, trusted-local Linux Docker
daemon (a native unix-socket engine in CI, or Docker Desktop's
``npipe:////./pipe/dockerDesktopLinuxEngine`` Linux engine on Windows --
both are approved by the broker's trust model, so this suite runs on
either):

  1. The funded ``react-vite`` dependency bundle (build -> verify -> clean).
  2. The source-blind ``oracle-playwright`` bundle, plus a real offline
     Chromium launch inside the pinned Playwright image (``--network none``).

Both contexts share the same cleanup guarantee: no funded container,
network, or volume may survive the run.
"""

from __future__ import annotations

import base64
import contextlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

from signalos_lib.product import dependency_broker as broker


_GATE = "SIGNALOS_RUN_DEPENDENCY_PROXY_INTEGRATION"
_LABEL_FILTERS = (
    "label=signalos.owner=dependency-broker",
    "label=signalos.scope=funded-dependency",
)
# Sandbox dependency volumes carry a distinct scope label (sandbox.py volume
# create). Both the funded and the source-blind oracle contexts may create
# them, so a leftover volume is residue too and must fail the run.
_VOLUME_FILTER = "label=signalos.scope=funded"
_SENSITIVE_ENV_RE = re.compile(
    r"(?:^|_)(?:API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|SECRET(?:_?KEY)?|TOKEN|"
    r"PASSWORD|PASSWD|CREDENTIALS?|AUTHORIZATION)(?:_|$)",
    re.IGNORECASE,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DRIVER_PATH = _REPO_ROOT / "scripts" / "backend_matrix" / "driver.py"


def _load_driver() -> ModuleType:
    """Import the backend-matrix driver script without a ``scripts`` package."""

    spec = importlib.util.spec_from_file_location(
        "signalos_backend_matrix_driver", _DRIVER_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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
class _TrustedLocalDockerIntegrationBase(unittest.TestCase):
    """Shared plumbing: approved endpoint discovery + residue accounting.

    The trust gate is the broker's own model -- a Linux daemon reached over
    an approved trusted-local endpoint (a unix socket, or Docker Desktop's
    Windows Linux-engine named pipe) -- not the host OS. That keeps the
    suite honest on the developer's Windows Docker Desktop as well as the
    Linux CI runner.
    """

    maxDiff = None

    def setUp(self) -> None:
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
        if endpoint in broker.UNIX_DOCKER_ENGINE_ENDPOINTS:
            self.host_trust_profile = broker.TRUSTED_LOCAL_DOCKER_ENGINE_PROFILE
        elif endpoint == broker.WINDOWS_DOCKER_DESKTOP_LINUX_ENDPOINT:
            self.host_trust_profile = broker.TRUSTED_LOCAL_DOCKER_DESKTOP_PROFILE
        else:
            self.fail(
                "Docker context is not an approved trusted-local Linux engine endpoint"
            )
        self.endpoint = endpoint
        daemon_os = json.loads(
            self._docker("info", "--format", "{{json .OSType}}").strip()
        )
        self.assertEqual(daemon_os, "linux")

        self.repo = _REPO_ROOT
        self.before = self._resource_snapshot()
        self.assertEqual(
            self.before,
            (frozenset(), frozenset(), frozenset()),
            "the integration daemon has stale funded-dependency resources",
        )

    def tearDown(self) -> None:
        if not hasattr(self, "before"):
            return
        after = self._resource_snapshot()
        self.assertEqual(
            after,
            self.before,
            f"funded Docker residue detected: before={self.before}, after={after}",
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

    def _resource_snapshot(
        self,
    ) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
        filters = [part for item in _LABEL_FILTERS for part in ("--filter", item)]
        containers = self._ids(self._docker("ps", "-aq", *filters))
        networks = self._ids(self._docker("network", "ls", "-q", *filters))
        volumes = self._names(
            self._docker("volume", "ls", "-q", "--filter", _VOLUME_FILTER)
        )
        return containers, networks, volumes

    def _ids(self, output: str) -> frozenset[str]:
        values = frozenset(
            line.strip().lower() for line in output.splitlines() if line.strip()
        )
        if any(re.fullmatch(r"[0-9a-f]{12,64}", value) is None for value in values):
            self.fail("Docker returned a malformed resource identifier")
        return values

    def _names(self, output: str) -> frozenset[str]:
        # Volume names are broker-generated tokens, not hex ids; any surviving
        # name under the funded scope label is residue.
        return frozenset(line.strip() for line in output.splitlines() if line.strip())

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


class DependencyBrokerDockerIntegrationTests(_TrustedLocalDockerIntegrationBase):
    """The funded react-vite dependency boundary against a real daemon."""

    def setUp(self) -> None:
        super().setUp()
        self.policy_path = (
            self.repo / "scripts" / "backend_matrix" / "dependencies" / "policy.json"
        )
        self.policy = broker.load_dependency_policy(self.policy_path)
        self._docker("image", "inspect", self.policy.image)

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
                        self.host_trust_profile,
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


class OracleRuntimeDockerIntegrationTests(_TrustedLocalDockerIntegrationBase):
    """The source-blind oracle boundary: real Playwright bundle + offline
    Chromium launch, driven through the production ``FundedRunContext``."""

    def setUp(self) -> None:
        super().setUp()
        self.driver = _load_driver()
        self.oracle_policy_path = self.driver.DEFAULT_ORACLE_DEPENDENCY_POLICY.resolve()
        self.oracle_policy = broker.load_dependency_policy(self.oracle_policy_path)
        self.assertEqual(
            self.oracle_policy.profile, self.driver.ORACLE_RUNTIME_PROFILE
        )
        # Pull policy is 'never'; the pinned Playwright image must be cached.
        self._docker("image", "inspect", self.oracle_policy.image)

    def test_oracle_bundle_builds_verifies_and_launches_chromium_offline(self) -> None:
        with _without_provider_credentials():
            context = self.driver.FundedRunContext.prepare(
                self.oracle_policy_path,
                timeout=900,
                expected_profile=self.driver.ORACLE_RUNTIME_PROFILE,
            )
            try:
                # prepare() already built AND independently re-verified the
                # bundle (it raises otherwise). Confirm the receipt binds to
                # the reviewed oracle policy bytes and the trusted local daemon.
                evidence = context.public_evidence()
                self.assertEqual(
                    evidence["policy_sha256"],
                    self.driver._sha256_file(self.oracle_policy_path),
                )
                self.assertEqual(
                    evidence["profile"], self.driver.ORACLE_RUNTIME_PROFILE
                )
                self.assertEqual(evidence["image"], self.oracle_policy.image)
                provisioner = evidence["provisioner"]
                self.assertEqual(
                    provisioner["host_trust_profile"], self.host_trust_profile
                )
                self.assertEqual(provisioner["daemon_os_type"], "linux")
                self.assertEqual(provisioner["pull_policy"], "never")
                self.assertEqual(provisioner["cleanup_verified"], True)

                # The keyless browser-readiness gate: launch Chromium fully
                # offline (`--network none`) inside the pinned image and
                # require the in-container success sentinel.
                probe = context.browser_runtime_probe(timeout=600)
                self.assertTrue(probe["ok"])
                self.assertIn(
                    "SIGNALOS_ORACLE_RUNTIME_OK",
                    str(probe.get("stdout_tail") or ""),
                )
            finally:
                cleanup = context.close()
        self.assertEqual(cleanup.get("scratch_removed"), True)
        # tearDown asserts no funded container / network / volume residue.


if __name__ == "__main__":
    unittest.main()
