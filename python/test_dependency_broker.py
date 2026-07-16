from __future__ import annotations

import json
import hashlib
import inspect
import os
import shutil
import stat
from dataclasses import replace
from pathlib import Path

import pytest
import signalos_lib.product.dependency_broker as broker

from signalos_lib.product.dependency_broker import (
    DependencyBrokerError,
    load_dependency_policy,
    materialize_dependency_bundle,
    prepare_dependency_bundle,
    validate_package_lock,
    verify_dependency_bundle,
    verify_materialized_dependencies,
)
from signalos_lib.product.sandbox import CommandOutput


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DEPENDENCIES = ROOT / "scripts" / "backend_matrix" / "dependencies"
ATTESTATION_KEY = b"signalos-funded-test-attestation-key-v1"


def _policy_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "dependencies"
    shutil.copytree(SOURCE_DEPENDENCIES, destination)
    return destination / "policy.json"


def _mutate_first_lock_entry(policy_path: Path, **changes) -> None:
    lock_path = policy_path.parent / "react-vite" / "package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    first = next(key for key in lock["packages"] if key)
    lock["packages"][first].update(changes)
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")


class _FakeInstaller:
    dependency_egress_policy = "npm-registry-connect-v2"
    platform = "linux/amd64"
    engine = "docker"
    image = (
        "docker.io/library/node:20-bookworm@sha256:"
        "cacf10e99285cbbc891452e31249c1b5ec3ba225f40028fae946b75aeaf1b66a"
    )
    proxy_image = image
    proxy_script_sha256 = hashlib.sha256(
        (SOURCE_DEPENDENCIES / "proxy" / "connect-proxy.cjs").read_bytes()
    ).hexdigest()

    def __init__(self) -> None:
        self.calls = []
        self.staging_mode = None
        self.input_modes = None

    def evidence(self) -> broker.TrustedDependencyRunEvidence:
        return broker.TrustedDependencyRunEvidence(
            schema=broker.RUNNER_EVIDENCE_SCHEMA,
            engine="docker",
            host_trust_profile=broker.TRUSTED_LOCAL_DOCKER_DESKTOP_PROFILE,
            docker_endpoint=broker.WINDOWS_DOCKER_DESKTOP_LINUX_ENDPOINT,
            daemon_os_type="linux",
            platform=self.platform,
            installer_image=self.image,
            proxy_image=self.proxy_image,
            runtime_image_id="sha256:" + "a" * 64,
            proxy_script_sha256=self.proxy_script_sha256,
            runner_sha256=broker._trusted_runner_sha256(),
            egress_policy=self.dependency_egress_policy,
            allowed_connect_authorities=(broker.APPROVED_CONNECT_AUTHORITY,),
            installer_network=broker.PROXY_INSTALLER_NETWORK,
            proxy_egress_network=broker.PROXY_EGRESS_NETWORK,
            tls_mode=broker.PROXY_TLS_MODE,
            pull_policy="never",
            cleanup_verified=True,
        )

    def run(self, command, cwd, timeout, env):
        self.calls.append((command, Path(cwd), timeout, dict(env)))
        self.staging_mode = stat.S_IMODE(Path(cwd).stat().st_mode)
        self.input_modes = {
            name: stat.S_IMODE((Path(cwd) / name).stat().st_mode)
            for name in ("package.json", "package-lock.json")
        }
        manifest = json.loads((Path(cwd) / "package.json").read_text(encoding="utf-8"))
        packages = {**manifest["dependencies"], **manifest["devDependencies"]}
        for name in packages:
            package = (Path(cwd) / "node_modules").joinpath(*name.split("/"))
            package.mkdir(parents=True)
            (package / "index.js").write_text(
                f"// trusted fixture for {name}\n", encoding="utf-8"
            )
        (Path(cwd) / "node_modules" / ".vite").mkdir(exist_ok=True)
        broker._write_dependency_archive(
            Path(cwd) / "node_modules", Path(cwd) / "node_modules.tar"
        )
        return (
            0,
            CommandOutput(
                stdout="SIGNALOS_RUNTIME=linux/x64\n10.8.2\n\nadded 1 package\n",
                stderr="",
            ),
            self.evidence(),
        )


@pytest.fixture(autouse=True)
def _fake_runner_factory(monkeypatch):
    created: list[_FakeInstaller] = []

    def factory(_policy):
        runner = _FakeInstaller()
        created.append(runner)
        return runner

    monkeypatch.setattr(broker, "_new_dependency_runner", factory)
    return created


def test_reviewed_policy_and_lock_are_strictly_valid() -> None:
    policy = load_dependency_policy(SOURCE_DEPENDENCIES / "policy.json")
    evidence = validate_package_lock(policy)

    assert evidence["lockfile_version"] == 3
    assert evidence["package_count"] == 236
    assert len(evidence["resolved_urls_sha256"]) == 64


def test_policy_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    policy_path = _policy_copy(tmp_path)
    policy_path.write_text(
        '{"schema":"signalos.funded-dependency-policy.v2",'
        '"schema":"signalos.funded-dependency-policy.v2"}\n',
        encoding="utf-8",
    )

    with pytest.raises(DependencyBrokerError, match="duplicate JSON key"):
        load_dependency_policy(policy_path)


@pytest.mark.parametrize(
    "resolved",
    [
        "http://registry.npmjs.org/pkg/-/pkg-1.0.0.tgz",
        "https://registry.npmjs.org.evil.invalid/pkg/-/pkg-1.0.0.tgz",
        "https://user@registry.npmjs.org/pkg/-/pkg-1.0.0.tgz",
        "https://registry.npmjs.org:444/pkg/-/pkg-1.0.0.tgz",
        "https://registry.npmjs.org/pkg/-/pkg-1.0.0.tgz?download=1",
        "https://registry.npmjs.org/pkg/-/pkg-1.0.0.tgz#fragment",
        "https://registry.npmjs.org/pkg/%2f/pkg-1.0.0.tgz",
        "https://registry.npmjs.org/pkg/../pkg-1.0.0.tgz",
        "https://registry.npmjs.org/pkg/index.json",
    ],
)
def test_lock_rejects_every_unapproved_resolved_url(
    tmp_path: Path, resolved: str
) -> None:
    policy_path = _policy_copy(tmp_path)
    _mutate_first_lock_entry(policy_path, resolved=resolved)

    with pytest.raises(DependencyBrokerError):
        validate_package_lock(load_dependency_policy(policy_path))


def test_lock_rejects_links_missing_integrity_and_non_exact_versions(tmp_path: Path) -> None:
    for changes, match in (
        ({"link": True}, "linked dependency"),
        ({"integrity": None}, "sha512 integrity"),
        ({"version": "latest"}, "exact version"),
    ):
        case = tmp_path / match.replace(" ", "-")
        case.mkdir()
        policy_path = _policy_copy(case)
        _mutate_first_lock_entry(policy_path, **changes)
        with pytest.raises(DependencyBrokerError, match=match):
            validate_package_lock(load_dependency_policy(policy_path))


def test_manifest_rejects_git_file_workspace_alias_and_tags(tmp_path: Path) -> None:
    for index, spec in enumerate(
        ("git+https://example.invalid/x.git", "file:../x", "workspace:*", "npm:x@1.0.0", "latest")
    ):
        case = tmp_path / str(index)
        case.mkdir()
        policy_path = _policy_copy(case)
        package_path = policy_path.parent / "react-vite" / "package.json"
        package = json.loads(package_path.read_text(encoding="utf-8"))
        package["dependencies"]["react"] = spec
        package_path.write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
        with pytest.raises(DependencyBrokerError, match="non-semver"):
            validate_package_lock(load_dependency_policy(policy_path))


def test_prepare_bundle_uses_only_fixed_scriptless_command_and_env(
    tmp_path: Path, _fake_runner_factory
) -> None:
    policy_path = _policy_copy(tmp_path)
    bundle = tmp_path / "bundle"

    receipt = prepare_dependency_bundle(
        policy_path,
        bundle,
        engine="docker",
        timeout=321,
        attestation_key=ATTESTATION_KEY,
    )
    runner = _fake_runner_factory[0]

    assert receipt["status"] == "ready"
    assert receipt["schema"] == "signalos.dependency-receipt.v3"
    assert receipt["provisioner"] == {
        "schema": broker.RUNNER_EVIDENCE_SCHEMA,
        "engine": "docker",
        "host_trust_profile": broker.TRUSTED_LOCAL_DOCKER_DESKTOP_PROFILE,
        "docker_endpoint": broker.WINDOWS_DOCKER_DESKTOP_LINUX_ENDPOINT,
        "daemon_os_type": "linux",
        "platform": "linux/amd64",
        "installer_image": runner.image,
        "proxy_image": runner.proxy_image,
        "runtime_image_id": "sha256:" + "a" * 64,
        "proxy_script_sha256": runner.proxy_script_sha256,
        "runner_sha256": broker._trusted_runner_sha256(),
        "egress_policy": "npm-registry-connect-v2",
        "allowed_connect_authorities": ["registry.npmjs.org:443"],
        "installer_network": "docker-internal",
        "proxy_egress_network": "dedicated-bridge",
        "tls_mode": "end-to-end-strict",
        "pull_policy": "never",
        "cleanup_verified": True,
    }
    assert len(runner.calls) == 1
    command, cwd, timeout, env = runner.calls[0]
    assert "npm --version && npm ci --ignore-scripts --no-audit --no-fund" in command
    assert "--no-recursion" in command and "--mtime='@0'" in command
    assert cwd.name.startswith(".bundle.staging-")
    assert timeout == 321
    if os.name != "nt":
        assert runner.staging_mode == 0o700
        assert runner.input_modes == {"package.json": 0o600, "package-lock.json": 0o600}
    assert env["NPM_CONFIG_IGNORE_SCRIPTS"] == "true"
    assert env["NPM_CONFIG_REGISTRY"] == "https://registry.npmjs.org/"
    assert all("KEY" not in key and "TOKEN" not in key for key in env)
    assert (bundle / "node_modules.tar").is_file()
    assert not (bundle / "node_modules").exists()
    assert verify_dependency_bundle(
        policy_path, bundle, attestation_key=ATTESTATION_KEY
    ) == receipt


@pytest.mark.parametrize(
    "field,value",
    [
        ("host_trust_profile", "unreviewed-local-host-v1"),
        ("docker_endpoint", "npipe:////./pipe/docker_engine"),
        ("docker_endpoint", [broker.WINDOWS_DOCKER_DESKTOP_LINUX_ENDPOINT]),
        ("daemon_os_type", "windows"),
    ],
)
def test_runner_rejects_untrusted_host_evidence(field: str, value: object) -> None:
    policy = load_dependency_policy(SOURCE_DEPENDENCIES / "policy.json")
    evidence = replace(_FakeInstaller().evidence(), **{field: value})

    with pytest.raises(DependencyBrokerError, match="host evidence is invalid"):
        broker._validate_runner_evidence(evidence, policy)


@pytest.mark.parametrize(
    "endpoint",
    ["unix:///var/run/docker.sock", "unix:///run/docker.sock"],
)
def test_runner_accepts_exact_unix_engine_host_evidence(endpoint: str) -> None:
    policy = load_dependency_policy(SOURCE_DEPENDENCIES / "policy.json")
    evidence = replace(
        _FakeInstaller().evidence(),
        host_trust_profile=broker.TRUSTED_LOCAL_DOCKER_ENGINE_PROFILE,
        docker_endpoint=endpoint,
    )

    receipt = broker._validate_runner_evidence(evidence, policy)

    assert receipt["host_trust_profile"] == broker.TRUSTED_LOCAL_DOCKER_ENGINE_PROFILE
    assert receipt["docker_endpoint"] == endpoint
    assert receipt["daemon_os_type"] == "linux"


def test_materialize_verifies_every_byte_and_detects_tampering(tmp_path: Path) -> None:
    policy_path = _policy_copy(tmp_path)
    bundle = tmp_path / "bundle"
    prepare_dependency_bundle(
        policy_path, bundle, engine="docker",
        attestation_key=ATTESTATION_KEY,
    )
    workspace = tmp_path / "workspace"
    (workspace / ".signalos").mkdir(parents=True)
    shutil.copy2(policy_path.parent / "react-vite" / "package.json", workspace / "package.json")
    shutil.copy2(
        policy_path.parent / "react-vite" / "package-lock.json",
        workspace / "package-lock.json",
    )

    receipt = materialize_dependency_bundle(
        workspace, policy_path, bundle, attestation_key=ATTESTATION_KEY
    )

    assert verify_materialized_dependencies(
        workspace, policy_path, attestation_key=ATTESTATION_KEY
    ) == receipt
    target = workspace / ".signalos" / "dependencies" / "node_modules.tar"
    with target.open("ab") as handle:
        handle.write(b"tampered\n")
    with pytest.raises(DependencyBrokerError, match="archive drifted"):
        verify_materialized_dependencies(
            workspace, policy_path, attestation_key=ATTESTATION_KEY
        )


def test_bundle_receipt_tampering_is_rejected(tmp_path: Path) -> None:
    policy_path = _policy_copy(tmp_path)
    bundle = tmp_path / "bundle"
    prepare_dependency_bundle(
        policy_path, bundle, engine="docker",
        attestation_key=ATTESTATION_KEY,
    )
    receipt_path = bundle / ".signalos-dependency-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["fetch"]["scripts_ignored"] = False
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(DependencyBrokerError, match="self-hash"):
        verify_dependency_bundle(
            policy_path, bundle, attestation_key=ATTESTATION_KEY
        )


def test_workspace_manifest_drift_blocks_before_materialization(tmp_path: Path) -> None:
    policy_path = _policy_copy(tmp_path)
    bundle = tmp_path / "bundle"
    prepare_dependency_bundle(
        policy_path, bundle, engine="docker",
        attestation_key=ATTESTATION_KEY,
    )
    workspace = tmp_path / "workspace"
    (workspace / ".signalos").mkdir(parents=True)
    (workspace / "package.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(DependencyBrokerError, match="reviewed scaffold"):
        materialize_dependency_bundle(
            workspace, policy_path, bundle, attestation_key=ATTESTATION_KEY
        )
    assert not (workspace / "node_modules").exists()


def test_funded_dependencies_pending_only_on_pristine_workspace(
    tmp_path: Path,
) -> None:
    # Pre-G4 gates run sandboxed commands before materialization: a pristine
    # workspace is "pending" (hardened container runs with no deps volume).
    assert broker.funded_dependencies_pending(tmp_path) is True
    # An EMPTY node_modules mountpoint is still pristine.
    (tmp_path / "node_modules").mkdir()
    assert broker.funded_dependencies_pending(tmp_path) is True
    # A populated node_modules is not pending -- fall through to strict
    # verification (which fails closed without a receipt).
    (tmp_path / "node_modules" / "left-pad").mkdir()
    assert broker.funded_dependencies_pending(tmp_path) is False


def test_funded_dependencies_pending_rejects_partial_artifacts(
    tmp_path: Path,
) -> None:
    # Any materialization artifact means NOT pending, so tampered or
    # half-materialized workspaces always reach strict verification.
    with_receipt = tmp_path / "with-receipt"
    (with_receipt / ".signalos").mkdir(parents=True)
    (with_receipt / ".signalos" / broker.RECEIPT_NAME).write_text(
        "{}", encoding="utf-8"
    )
    assert broker.funded_dependencies_pending(with_receipt) is False

    with_archive = tmp_path / "with-archive"
    (with_archive / ".signalos" / "dependencies").mkdir(parents=True)
    (with_archive / ".signalos" / "dependencies" / broker.ARCHIVE_NAME).write_bytes(
        b""
    )
    assert broker.funded_dependencies_pending(with_archive) is False


def test_when_materialized_verify_skips_pristine_but_fails_closed_on_partial(
    tmp_path: Path,
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    # Regression (funded canary run 2): agent_loop's per-command
    # belt-and-braces receipt check called the strict verifier directly, so a
    # pre-G4 run_command on a pristine workspace died with "materialized
    # dependency receipt is missing or unreadable" even after the sandbox
    # mount itself was gated on funded_dependencies_pending.
    monkeypatch.setenv("SIGNALOS_SANDBOX_PROFILE", "funded")
    monkeypatch.setenv(
        "SIGNALOS_DEPENDENCY_POLICY",
        str(SOURCE_DEPENDENCIES / "policy.json"),
    )
    workspace = tmp_path / "workspace"
    (workspace / ".signalos").mkdir(parents=True)

    # Pristine (pre-G4): nothing to verify, must NOT raise.
    assert broker.verify_funded_dependencies_when_materialized(workspace) is None

    # Partial materialization (a receipt with no verifiable bundle) is tamper
    # evidence -- strict verification still fails closed.
    (workspace / ".signalos" / broker.RECEIPT_NAME).write_text(
        "{}", encoding="utf-8"
    )
    with pytest.raises(DependencyBrokerError):
        broker.verify_funded_dependencies_when_materialized(workspace)


def test_react_vite_scaffold_package_json_matches_funded_fixture(
    tmp_path: Path,
) -> None:
    # materialize_dependency_bundle sha-checks the workspace package.json
    # byte-for-byte against the reviewed dependency fixture, so the delivery
    # scaffold must emit that fixture EXACTLY -- with LF endings on every
    # platform. Regression: Path.write_text translated \n -> \r\n on Windows,
    # so the scaffold produced a CRLF package.json that failed the funded
    # "workspace package.json does not match the reviewed scaffold" check
    # (Linux/CI is LF, so it only bit Windows funded hosts).
    from signalos_lib.product import stacks

    fixture = (SOURCE_DEPENDENCIES / "react-vite" / "package.json").read_bytes()
    adapter = stacks.get_adapter("react-vite")
    adapter.scaffold(tmp_path, {"product_name": "Fixture Parity"})
    produced = (tmp_path / "package.json").read_bytes()

    assert produced == fixture
    assert b"\r\n" not in produced


def test_prepare_api_has_no_caller_supplied_runner_boundary() -> None:
    parameters = inspect.signature(prepare_dependency_bundle).parameters

    assert "runner" not in parameters


def test_broker_rehashes_staged_inputs_after_runner_returns(
    tmp_path: Path, monkeypatch
) -> None:
    class MutatingInstaller(_FakeInstaller):
        def run(self, command, cwd, timeout, env):
            result = super().run(command, cwd, timeout, env)
            (Path(cwd) / "package.json").write_text("{}\n", encoding="utf-8")
            return result

    monkeypatch.setattr(broker, "_new_dependency_runner", lambda _policy: MutatingInstaller())
    policy_path = _policy_copy(tmp_path)

    with pytest.raises(DependencyBrokerError, match="staged dependency inputs changed"):
        prepare_dependency_bundle(
            policy_path,
            tmp_path / "bundle",
            engine="docker",
            attestation_key=ATTESTATION_KEY,
        )


def test_broker_rehashes_reviewed_sources_after_runner_returns(
    tmp_path: Path, monkeypatch
) -> None:
    class SourceMutatingInstaller(_FakeInstaller):
        def __init__(self, lock_path: Path) -> None:
            super().__init__()
            self.lock_path = lock_path

        def run(self, command, cwd, timeout, env):
            result = super().run(command, cwd, timeout, env)
            self.lock_path.write_bytes(self.lock_path.read_bytes() + b" ")
            return result

    policy_path = _policy_copy(tmp_path)
    lock_path = policy_path.parent / "react-vite" / "package-lock.json"
    monkeypatch.setattr(
        broker,
        "_new_dependency_runner",
        lambda _policy: SourceMutatingInstaller(lock_path),
    )

    with pytest.raises(DependencyBrokerError):
        prepare_dependency_bundle(
            policy_path,
            tmp_path / "bundle",
            engine="docker",
            attestation_key=ATTESTATION_KEY,
        )


def test_recomputed_public_receipt_hash_cannot_forge_provenance(tmp_path: Path) -> None:
    policy_path = _policy_copy(tmp_path)
    bundle = tmp_path / "bundle"
    prepare_dependency_bundle(
        policy_path, bundle, engine="docker",
        attestation_key=ATTESTATION_KEY,
    )
    receipt_path = bundle / ".signalos-dependency-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["fetch"]["audit"] = True
    unsigned = dict(receipt)
    unsigned.pop("receipt_sha256", None)
    receipt["receipt_sha256"] = hashlib.sha256(
        json.dumps(
            unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(DependencyBrokerError, match="provenance HMAC"):
        verify_dependency_bundle(
            policy_path, bundle, attestation_key=ATTESTATION_KEY
        )


def test_runner_topology_tampering_cannot_forge_provenance(tmp_path: Path) -> None:
    policy_path = _policy_copy(tmp_path)
    bundle = tmp_path / "bundle"
    prepare_dependency_bundle(
        policy_path, bundle, engine="docker",
        attestation_key=ATTESTATION_KEY,
    )
    receipt_path = bundle / ".signalos-dependency-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["provisioner"]["installer_network"] = "bridge"
    unsigned = dict(receipt)
    unsigned.pop("receipt_sha256", None)
    receipt["receipt_sha256"] = hashlib.sha256(
        json.dumps(
            unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(DependencyBrokerError, match="provenance HMAC"):
        verify_dependency_bundle(
            policy_path, bundle, attestation_key=ATTESTATION_KEY
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("host_trust_profile", "unreviewed-local-host-v1"),
        ("docker_endpoint", "npipe:////./pipe/docker_engine"),
        ("docker_endpoint", [broker.WINDOWS_DOCKER_DESKTOP_LINUX_ENDPOINT]),
        ("daemon_os_type", "windows"),
    ],
)
def test_signed_receipt_rejects_untrusted_host_evidence(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    policy_path = _policy_copy(tmp_path)
    bundle = tmp_path / "bundle"
    prepare_dependency_bundle(
        policy_path,
        bundle,
        engine="docker",
        attestation_key=ATTESTATION_KEY,
    )
    receipt_path = bundle / ".signalos-dependency-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["provisioner"][field] = value
    receipt["provenance_hmac_sha256"] = broker._receipt_mac(
        receipt,
        ATTESTATION_KEY,
    )
    receipt["receipt_sha256"] = broker._receipt_hash(receipt)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(DependencyBrokerError, match="host evidence is invalid"):
        verify_dependency_bundle(
            policy_path,
            bundle,
            attestation_key=ATTESTATION_KEY,
        )


def test_wrong_attestation_key_is_rejected(tmp_path: Path) -> None:
    policy_path = _policy_copy(tmp_path)
    bundle = tmp_path / "bundle"
    prepare_dependency_bundle(
        policy_path, bundle, engine="docker",
        attestation_key=ATTESTATION_KEY,
    )
    with pytest.raises(DependencyBrokerError, match="HMAC"):
        verify_dependency_bundle(
            policy_path, bundle, attestation_key=b"x" * 32
        )


def test_preexisting_node_modules_symlink_is_never_materialized(tmp_path: Path) -> None:
    policy_path = _policy_copy(tmp_path)
    bundle = tmp_path / "bundle"
    prepare_dependency_bundle(
        policy_path, bundle, engine="docker",
        attestation_key=ATTESTATION_KEY,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    shutil.copy2(policy_path.parent / "react-vite" / "package.json", workspace / "package.json")
    target = workspace / "dist" / "deps"
    target.mkdir(parents=True)
    try:
        (workspace / "node_modules").symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are not available on this host")

    with pytest.raises(DependencyBrokerError, match="must be absent"):
        materialize_dependency_bundle(
            workspace, policy_path, bundle, attestation_key=ATTESTATION_KEY
        )


def test_materialization_never_follows_governance_directory_symlink(tmp_path: Path) -> None:
    policy_path = _policy_copy(tmp_path)
    bundle = tmp_path / "bundle"
    prepare_dependency_bundle(
        policy_path, bundle, engine="docker",
        attestation_key=ATTESTATION_KEY,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    shutil.copy2(policy_path.parent / "react-vite" / "package.json", workspace / "package.json")
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (workspace / ".signalos").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are not available on this host")

    with pytest.raises(DependencyBrokerError, match="non-reparse directory"):
        materialize_dependency_bundle(
            workspace, policy_path, bundle, attestation_key=ATTESTATION_KEY
        )
    assert list(outside.iterdir()) == []


def test_dependency_tree_hash_binds_executable_mode(tmp_path: Path) -> None:
    root = tmp_path / "node_modules"
    root.mkdir()
    executable = root / "tool"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o644)
    before = broker._dependency_tree(root, max_files=10, max_bytes=1024)
    executable.chmod(0o755)
    after = broker._dependency_tree(root, max_files=10, max_bytes=1024)
    if stat.S_IMODE(executable.stat().st_mode) & 0o111 == 0:
        pytest.skip("host filesystem does not preserve executable mode bits")
    assert before.sha256 != after.sha256


def test_materialization_filesystem_errors_are_typed(tmp_path: Path, monkeypatch) -> None:
    policy_path = _policy_copy(tmp_path)
    bundle = tmp_path / "bundle"
    prepare_dependency_bundle(
        policy_path, bundle, engine="docker",
        attestation_key=ATTESTATION_KEY,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    shutil.copy2(policy_path.parent / "react-vite" / "package.json", workspace / "package.json")
    original_copy2 = shutil.copy2

    def fail_archive(source, destination, *args, **kwargs):
        if Path(source).name == "node_modules.tar":
            raise OSError("disk denied")
        return original_copy2(source, destination, *args, **kwargs)

    monkeypatch.setattr(shutil, "copy2", fail_archive)
    with pytest.raises(DependencyBrokerError, match="materialization failed"):
        materialize_dependency_bundle(
            workspace, policy_path, bundle, attestation_key=ATTESTATION_KEY
        )
