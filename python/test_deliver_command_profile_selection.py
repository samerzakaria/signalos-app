"""Tests for Deliver command profile selection helpers."""

from __future__ import annotations

import argparse
import json
from contextlib import redirect_stdout
from io import StringIO

from signalos_lib.commands.deliver import cmd_deliver_design


def _run_design(prompt: str, repo_root, profile: str = "auto") -> dict:
    args = argparse.Namespace(
        prompt=prompt,
        name=None,
        repo_root=str(repo_root),
        profile=profile,
        as_json=True,
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = cmd_deliver_design(args)
    assert rc == 0
    return json.loads(out.getvalue())


def test_deliver_design_auto_profile_can_choose_generic(tmp_path):
    payload = _run_design(
        "Build a Python checksum library for validating uploaded files",
        tmp_path,
    )

    assert payload["profile"] == "generic"


def test_deliver_design_auto_profile_can_choose_ui_product(tmp_path):
    payload = _run_design(
        "Build a dashboard to manage team tasks, utilization, workload, and KPIs",
        tmp_path,
    )

    assert payload["profile"] == "react-vite"
