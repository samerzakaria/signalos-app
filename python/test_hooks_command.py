from __future__ import annotations

import json
from pathlib import Path

from signalos_lib.commands.hooks import main as hooks_main


def _write_hooks_json(root: Path, hooks: list[dict]) -> None:
    target = root / "core" / "tool-adapters" / "_shared" / "hooks.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(hooks), encoding="utf-8")


def test_hooks_test_fails_closed_when_script_is_missing(tmp_path: Path, capsys) -> None:
    """A registered hook whose script file is absent is broken wiring: the
    dry-run must report it failed and exit nonzero, never skip-pass it."""
    _write_hooks_json(tmp_path, [
        {"name": "pre-tool-use-guard", "source": "core/execution/hooks/missing-guard.sh"},
    ])

    rc = hooks_main(["test", "--repo-root", str(tmp_path), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    result = payload["results"][0]
    assert result["passed"] is False
    assert result["skipped"] is True
    assert "missing-guard.sh" in result["reason"]
