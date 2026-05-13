import tempfile
import unittest
from pathlib import Path

from signalos_secret_guard import (
    REDACTED,
    is_secret_path,
    redact_arg_list,
    redact_for_model,
    redact_response,
    redact_text,
    scan_secret_files,
    summarize_env_text,
)


class SecretGuardTests(unittest.TestCase):
    def test_env_files_are_secret_paths(self):
        self.assertTrue(is_secret_path(".env"))
        self.assertTrue(is_secret_path(".env.production"))
        self.assertTrue(is_secret_path("private.pem"))
        self.assertFalse(is_secret_path("README.md"))

    def test_env_summary_keeps_names_only(self):
        text = "DATABASE_URL=postgres://user:pass@host/db\nPUBLIC_URL=https://example.com\n"
        self.assertEqual(
            summarize_env_text(text),
            f"DATABASE_URL={REDACTED}\nPUBLIC_URL={REDACTED}",
        )

    def test_env_source_is_never_passed_raw(self):
        text = "NEXT_PUBLIC_API_URL=https://example.com\nDATABASE_URL=postgres://u:p@db/app"
        redacted = redact_for_model(text, ".env")
        self.assertIn(f"NEXT_PUBLIC_API_URL={REDACTED}", redacted)
        self.assertIn(f"DATABASE_URL={REDACTED}", redacted)
        self.assertNotIn("postgres://u:p@db/app", redacted)

    def test_likely_secret_values_are_redacted(self):
        redacted = redact_text("OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456")
        self.assertEqual(redacted, f"OPENAI_API_KEY={REDACTED}")

    def test_nested_response_is_redacted(self):
        value = {"output": ["DATABASE_URL=postgres://user:pass@host/db"]}
        self.assertEqual(redact_response(value), {"output": [f"DATABASE_URL={REDACTED}"]})

    def test_json_secret_fields_are_redacted(self):
        value = '{"apiKey":"sk-abcdefghijklmnopqrstuvwxyz123456","name":"demo"}'
        self.assertEqual(redact_text(value), f'{{"apiKey":"{REDACTED}","name":"demo"}}')

    def test_args_are_redacted_before_command_dispatch(self):
        args = redact_arg_list(["API_TOKEN=secretsecretsecretsecretsecretsecret"])
        self.assertEqual(args, [f"API_TOKEN={REDACTED}"])

    def test_scan_secret_files_reports_names_not_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("DB_URL=postgres://user:pass@host/db\n", encoding="utf-8")
            found = scan_secret_files(tmp)
        self.assertEqual(found[0]["path"], ".env")
        self.assertEqual(found[0]["variables"], ["DB_URL"])

    def test_scan_secret_files_handles_utf8_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("\ufeffDATABASE_URL=postgres://user:pass@host/db\n", encoding="utf-8")
            found = scan_secret_files(tmp)
        self.assertEqual(found[0]["variables"], ["DATABASE_URL"])


if __name__ == "__main__":
    unittest.main()
