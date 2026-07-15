from __future__ import annotations

import json
import hashlib
import shutil
import stat
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
    dependency_egress_policy = "npm-registry-proxy-v1"
    platform = "linux/amd64"
    engine = "docker"
    image = (
        "docker.io/library/node:20-bookworm@sha256:"
        "cacf10e99285cbbc891452e31249c1b5ec3ba225f40028fae946b75aeaf1b66a"
    )

    def __init__(self) -> None:
        self.calls = []

    def run(self, command, cwd, timeout, env):
        self.calls.append((command, Path(cwd), timeout, dict(env)))
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
        return 0, CommandOutput(
            stdout="SIGNALOS_RUNTIME=linux/x64\n10.8.2\n\nadded 1 package\n",
            stderr="",
        )


def test_reviewed_policy_and_lock_are_strictly_valid() -> None:
    policy = load_dependency_policy(SOURCE_DEPENDENCIES / "policy.json")
    evidence = validate_package_lock(policy)

    assert evidence["lockfile_version"] == 3
    assert evidence["package_count"] == 236
    assert len(evidence["resolved_urls_sha256"]) == 64


def test_policy_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    policy_path = _policy_copy(tmp_path)
    policy_path.write_text(
        '{"schema":"signalos.funded-dependency-policy.v1",'
        '"schema":"signalos.funded-dependency-policy.v1"}\n',
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


def test_prepare_bundle_uses_only_fixed_scriptless_command_and_env(tmp_path: Path) -> None:
    policy_path = _policy_copy(tmp_path)
    runner = _FakeInstaller()
    bundle = tmp_path / "bundle"

    receipt = prepare_dependency_bundle(
        policy_path,
        bundle,
        engine="docker",
        runner=runner,
        timeout=321,
        attestation_key=ATTESTATION_KEY,
    )

    assert receipt["status"] == "ready"
    assert len(runner.calls) == 1
    command, cwd, timeout, env = runner.calls[0]
    assert "npm --version && npm ci --ignore-scripts --no-audit --no-fund" in command
    assert "--no-recursion" in command and "--mtime='@0'" in command
    assert cwd.name.startswith(".bundle.staging-")
    assert timeout == 321
    assert env["NPM_CONFIG_IGNORE_SCRIPTS"] == "true"
    assert env["NPM_CONFIG_REGISTRY"] == "https://registry.npmjs.org/"
    assert all("KEY" not in key and "TOKEN" not in key for key in env)
    assert (bundle / "node_modules.tar").is_file()
    assert not (bundle / "node_modules").exists()
    assert verify_dependency_bundle(
        policy_path, bundle, attestation_key=ATTESTATION_KEY
    ) == receipt


def test_materialize_verifies_every_byte_and_detects_tampering(tmp_path: Path) -> None:
    policy_path = _policy_copy(tmp_path)
    bundle = tmp_path / "bundle"
    prepare_dependency_bundle(
        policy_path, bundle, engine="docker", runner=_FakeInstaller(),
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
        policy_path, bundle, engine="docker", runner=_FakeInstaller(),
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
        policy_path, bundle, engine="docker", runner=_FakeInstaller(),
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


def test_prepare_fails_closed_without_registry_proxy_runner(tmp_path: Path) -> None:
    policy_path = _policy_copy(tmp_path)
    with pytest.raises(DependencyBrokerError, match="registry-proxy runner"):
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
        policy_path, bundle, engine="docker", runner=_FakeInstaller(),
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


def test_wrong_attestation_key_is_rejected(tmp_path: Path) -> None:
    policy_path = _policy_copy(tmp_path)
    bundle = tmp_path / "bundle"
    prepare_dependency_bundle(
        policy_path, bundle, engine="docker", runner=_FakeInstaller(),
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
        policy_path, bundle, engine="docker", runner=_FakeInstaller(),
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
        policy_path, bundle, engine="docker", runner=_FakeInstaller(),
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
        policy_path, bundle, engine="docker", runner=_FakeInstaller(),
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
