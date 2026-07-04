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
    ENFORCEMENT_REL,
    RUNTIME_RULES,
    FileEnforcementProvider,
    StaticEnforcementProvider,
    seed_trust_tier_paths,
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


if __name__ == "__main__":
    unittest.main()
