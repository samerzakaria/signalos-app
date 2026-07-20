# test_file_enforcement_provider.py
# Phase 1 / #15 — FileEnforcementProvider tests.
#
# Proves the Python read-side of the enforcement spine:
#   - configured rule modes in .signalos/enforcement.json are read back
#   - a core invariant set to "off" on disk STILL reads as strict (the floor is
#     re-applied on read; this is the anti-weakening proof)
#   - an absent file defaults every runtime rule to strict (== StaticEnforcementProvider)
#   - a corrupt file raises RuntimeError (INV-4: no silent fallback)
#   - CORE_INVARIANTS_PY set-equals the Rust CORE_INVARIANTS (locks the two floors)
#
# INV-6: no Tauri, no network. Pure filesystem.

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.enforcement_state import (  # noqa: E402
    CORE_INVARIANTS_PY,
    DEFAULT_TRUST_TIER_PATHS,
    ENFORCEMENT_REL,
    RUNTIME_RULES,
    FileEnforcementProvider,
    StaticEnforcementProvider,
    seed_trust_tier_paths,
)

# The real matchers the agent loop applies against the allowlists (imported so
# these tests exercise the same code path production does, not a re-implementation).
from signalos_lib.product.agent_loop import (  # noqa: E402
    _command_matches,
    _matches_glob,
)

_RUST_ENFORCEMENT = (
    Path(__file__).resolve().parent.parent / "src-tauri" / "src" / "enforcement.rs"
)


def _write_enforcement(root: Path, rule_modes: dict, wave_frozen: bool = False) -> None:
    path = root / ENFORCEMENT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"rule_modes": rule_modes, "wave_frozen": wave_frozen}),
        encoding="utf-8",
    )


class TestFileEnforcementProvider(unittest.TestCase):
    def test_reads_configured_modes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            _write_enforcement(
                root, {"stack-contract": "warn", "mutation-threshold": "off"}
            )
            state = FileEnforcementProvider().get_enforcement_state(root)
            self.assertEqual(state.rule_mode("stack-contract"), "warn")
            self.assertEqual(state.rule_mode("mutation-threshold"), "off")
            self.assertFalse(state.rule_enabled("mutation-threshold"))

    def test_core_invariant_off_reads_as_strict(self):
        # The anti-weakening proof: even if the file says gate-gating:off, the
        # floor forces it back to strict and rule_enabled stays True.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            _write_enforcement(root, {"gate-gating": "off"})
            state = FileEnforcementProvider().get_enforcement_state(root)
            self.assertEqual(state.rule_mode("gate-gating"), "strict")
            self.assertTrue(state.rule_enabled("gate-gating"))

    def test_absent_file_defaults_all_strict(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            file_state = FileEnforcementProvider().get_enforcement_state(root)
            static_state = StaticEnforcementProvider().get_enforcement_state(root)
            for rule in RUNTIME_RULES:
                self.assertEqual(file_state.rule_mode(rule), "strict", rule)
                self.assertEqual(
                    file_state.rule_mode(rule), static_state.rule_mode(rule), rule
                )

    def test_corrupt_file_raises(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            path = root / ENFORCEMENT_REL
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{ this is not json", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                FileEnforcementProvider().get_enforcement_state(root)

    def test_wave_frozen_read_from_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            _write_enforcement(root, {"stack-contract": "warn"}, wave_frozen=True)
            state = FileEnforcementProvider().get_enforcement_state(root)
            self.assertTrue(state.wave_frozen)

    def test_forbidden_lists_match_static_provider(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_trust_tier_paths(root)
            file_state = FileEnforcementProvider().get_enforcement_state(root)
            static_state = StaticEnforcementProvider().get_enforcement_state(root)
            self.assertEqual(
                sorted(file_state.forbidden_paths),
                sorted(static_state.forbidden_paths),
            )
            self.assertEqual(
                sorted(file_state.forbidden_actions),
                sorted(static_state.forbidden_actions),
            )

    def test_core_invariants_py_matches_rust(self):
        # Locks the two floors together so they can't drift.
        text = _RUST_ENFORCEMENT.read_text(encoding="utf-8")
        m = re.search(
            r"const CORE_INVARIANTS:\s*&\[&str\]\s*=\s*&\[(.*?)\];",
            text,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "could not locate CORE_INVARIANTS in enforcement.rs")
        body = m.group(1)
        # Map each RULE_* const referenced to its string literal.
        const_map = dict(
            re.findall(
                r'pub const (RULE_[A-Z_]+):\s*&str\s*=\s*"([^"]+)";', text
            )
        )
        rust_core = set()
        for token in re.findall(r"RULE_[A-Z_]+", body):
            self.assertIn(token, const_map, token)
            rust_core.add(const_map[token])
        self.assertEqual(rust_core, set(CORE_INVARIANTS_PY))


class TestT2StackAllowlist(unittest.TestCase):
    """The T2 write/execute allowlists must cover the files and validation
    commands the shipped stack adapters actually produce -- production runs at
    T2, so a scaffold/model file or a validation command that is not covered is
    silently DENIED (observed: stack config writes + `python -m compileall`
    rejected mid-build). These load the allowlist through the real on-disk seed
    + the real agent-loop matchers, so they prove the enforced policy, not a
    copy of it.
    """

    def _t2(self, root: Path) -> tuple[list[str], list[str]]:
        seed_trust_tier_paths(root)
        state = StaticEnforcementProvider(trust_tier="T2").get_enforcement_state(root)
        return state.tier_paths("write"), state.tier_paths("execute")

    # -- writes -------------------------------------------------------------

    # Representative files each shipped adapter's scaffold() writes (root-level
    # stack config/entry files + framework source dirs outside src/).
    _WRITABLE = [
        "index.html",            # react-vite / vue
        "index.css",
        "vite.config.ts",        # react-vite / vue
        "vite.config.cjs",       # EROFS-safe funded scaffold config (extension-agnostic)
        "vite.config.mjs",
        "vitest.config.ts",
        "vitest.config.mjs",     # valid ESM config name under "type":"module"
        "vitest.config.cjs",
        "jest.config.ts",        # jest stacks
        "eslint.config.js",      # ESLint 9 flat config
        "tsconfig.json",         # ts stacks
        "tsconfig.app.json",     # angular
        "tsconfig.vitest.json",  # tsconfig.*.json glob
        "angular.json",          # angular
        "next.config.js",        # nextjs
        "next.config.mjs",
        "next-env.d.ts",
        "app/layout.tsx",        # nextjs app router
        "app/page.tsx",
        "pages/index.tsx",       # nextjs pages router
        "components/Button.tsx",
        "styles/globals.css",
        "postcss.config.js",
        "tailwind.config.ts",
        ".eslintrc.json",
        "README.md",
        "pyproject.toml",        # generic / fastapi / django / flask
        "setup.cfg",
        "requirements-dev.txt",
        "manage.py",             # django
        "Cargo.toml",            # rust
        "go.mod",                # go
        "go.sum",
        "cmd/server/main.go",    # go entry
        "internal/app/app.go",   # go handler
        "pom.xml",               # java / spring
        "build.gradle",
        "SignalOSProduct.Api/Program.cs",                       # .net source
        "SignalOSProduct.Api/SignalOSProduct.Api.csproj",       # .net project
        "pubspec.yaml",          # flutter
        "analysis_options.yaml",
        "lib/main.dart",         # flutter source
        "test/widget_test.dart", # flutter test dir (singular)
        "app.json",              # expo
        "App.tsx",               # expo/react-native root entry
        "src/App.tsx",           # generic product source
    ]

    # Paths that must STAY denied -- traversal / absolute / outside-tree.
    _DENIED = [
        "/etc/passwd",
        "../secrets",
        "../../gold/hidden.test.ts",
        "~/.ssh/id_rsa",
        "secret/keys.txt",
    ]

    def test_representative_stack_files_are_writable_at_t2(self):
        with tempfile.TemporaryDirectory() as d:
            write_allow, _ = self._t2(Path(d))
            for path in self._WRITABLE:
                self.assertTrue(
                    _matches_glob(path, write_allow),
                    f"{path!r} should be writable under the T2 write allowlist",
                )

    def test_out_of_scope_paths_still_denied_at_t2(self):
        with tempfile.TemporaryDirectory() as d:
            write_allow, _ = self._t2(Path(d))
            for path in self._DENIED:
                self.assertFalse(
                    _matches_glob(path, write_allow),
                    f"{path!r} must NOT be granted by the T2 write allowlist",
                )

    def test_supply_chain_and_ci_config_stays_denied_at_t2(self):
        # SCOPING: broadening the build-config globs (vite/vitest/jest/...) must
        # NOT open supply-chain / CI / registry config. These stay governance-
        # owned (install-lifecycle & release surface) -- a model builds the
        # product, it does not author how the product is installed, shipped, or
        # gated. Generic agent tools allow these; SignalOS deliberately does not.
        supply_chain_denied = [
            ".github/workflows/ci.yml",
            ".gitlab-ci.yml",
            "Dockerfile",
            "docker-compose.yml",
            ".npmrc",
            ".yarnrc.yml",
        ]
        with tempfile.TemporaryDirectory() as d:
            write_allow, _ = self._t2(Path(d))
            for path in supply_chain_denied:
                self.assertFalse(
                    _matches_glob(path, write_allow),
                    f"{path!r} is governance-owned and must NOT be writable at T2",
                )

    def test_t2_write_allowlist_is_not_a_wildcard(self):
        # Governance must stay principled: extending the allowlist must never
        # collapse to "**" (which would allow any path, e.g. /etc/passwd).
        self.assertNotIn("**", DEFAULT_TRUST_TIER_PATHS["T2"]["write"])
        with tempfile.TemporaryDirectory() as d:
            write_allow, _ = self._t2(Path(d))
            self.assertNotIn("**", write_allow)

    # -- executes -----------------------------------------------------------

    # Real validation/install commands the shipped adapters' validation_plan()
    # emits (cross-checked against stacks.py). Each must match the T2 execute
    # allowlist or the per-task green loop runs blind.
    _EXECUTABLE = [
        'python -c "import x"',                    # generic build guard
        "python -m compileall src tests",          # generic / py-api build
        "python -m unittest discover -s tests",    # generic test
        'python -m pip install -e ".[dev]"',       # fastapi/django/flask install
        "node --check src/app.js",                 # node/expo build
        "node --test",                             # node/expo test
        "cargo build",                             # rust build
        "go build ./...",                          # go build (existing-repo)
        "golangci-lint run",                       # go lint
        "ruff check .",                            # python lint
        "npm run lint",                            # node lint
        "dotnet restore SignalOSProduct.Api/SignalOSProduct.Api.csproj",
        "dotnet build SignalOSProduct.Api/SignalOSProduct.Api.csproj --no-restore",
        "dotnet run --project SignalOSProduct.Api/SignalOSProduct.Api.csproj --no-build -- --self-test",
        "javac -d build/classes src/main/java/com/signalos/product/ProductServer.java",
        "java -cp build/classes com.signalos.product.ProductServerTest",
        "mvn -q test",                             # spring test
        "mvn -q -DskipTests package",              # spring build
        "flutter pub get",                         # flutter install
        "flutter analyze",                         # flutter build
        "flutter test",                            # flutter test
    ]

    def test_adapter_validation_commands_on_execute_allowlist(self):
        with tempfile.TemporaryDirectory() as d:
            _, execute_allow = self._t2(Path(d))
            for cmd in self._EXECUTABLE:
                self.assertTrue(
                    _command_matches(cmd, execute_allow),
                    f"{cmd!r} should match the T2 execute allowlist",
                )

    def test_t2_execute_allowlist_is_not_a_wildcard(self):
        self.assertNotIn("**", DEFAULT_TRUST_TIER_PATHS["T2"]["execute"])
        with tempfile.TemporaryDirectory() as d:
            _, execute_allow = self._t2(Path(d))
            self.assertNotIn("**", execute_allow)
            # A genuinely destructive command is still NOT matched by the
            # (non-wildcard) execute allowlist.
            self.assertFalse(_command_matches("rm -rf /", execute_allow))


if __name__ == "__main__":
    unittest.main()
