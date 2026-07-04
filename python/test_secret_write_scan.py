# test_secret_write_scan.py
# #20a value-aware write secret scan: BLOCK a hardcoded secret literal, but do
# NOT false-positive on ordinary generated code that merely mentions a
# secret-ish variable name (the data-leak block must not break generation).
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.product.agent_loop import _scan_write_secrets


class TestSecretWriteScan(unittest.TestCase):
    # Ordinary generated code that names a var token/secret/password but assigns
    # a non-literal (call/null/identifier/env/placeholder/prose) — must PASS.
    BENIGN = [
        "const token = response.headers.get('x-request-id');",
        "let sessionToken: string | null = null;",
        "const authToken = await getToken();",
        "password: userEnteredPassword,",
        "apiKey: process.env.API_KEY,",
        'const label = "Enter your password";',
        'apiKey: "YOUR_API_KEY_HERE",',
        'password = "";',
        'const description = "total expenses per category";',
        "const [token, setToken] = useState<string | null>(null);",
    ]
    # Real hardcoded secret literals — must BLOCK.
    SECRETS = [
        'const STRIPE_SECRET_KEY = "sk_live_abc123def456ghijklmno";',
        'const password = "hunter2mypassword99xx";',
        'const token = "abc123def456ghij789klmno";',
        'apiKey: "AIzaSyA1b2C3d4E5f6G7h8I9j0KLMNOPqrstu",',
        'const ghKey = "ghp_abcdefghijklmnopqrstuvwxyz0123456789";',
    ]

    def test_benign_code_is_not_flagged(self) -> None:
        for line in self.BENIGN:
            self.assertEqual(
                _scan_write_secrets("App.tsx", line), [],
                msg=f"false positive on benign line: {line!r}",
            )

    def test_hardcoded_secret_literals_are_flagged(self) -> None:
        for line in self.SECRETS:
            self.assertTrue(
                _scan_write_secrets("App.tsx", line),
                msg=f"missed a hardcoded secret: {line!r}",
            )

    def test_realistic_component_passes(self) -> None:
        component = (
            "import { useState } from 'react';\n"
            "export function ExpenseForm() {\n"
            "  const [token, setToken] = useState<string|null>(null);\n"
            "  const authToken = await getToken();\n"
            "  const password = form.password;\n"
            "  return <input aria-label='password' />;\n"
            "}\n"
        )
        self.assertEqual(_scan_write_secrets("src/components/ExpenseForm.tsx", component), [])


if __name__ == "__main__":
    unittest.main()
