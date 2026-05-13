import base64
import json
import unittest
import zipfile
from io import BytesIO

from signalos_attachments import analyze_payload


def payload(name, content, media_type="text/plain"):
    raw = content if isinstance(content, bytes) else content.encode("utf-8")
    return {
        "name": name,
        "type": media_type,
        "size": len(raw),
        "data_base64": base64.b64encode(raw).decode("ascii"),
    }


class AttachmentTests(unittest.TestCase):
    def test_blocks_env_files(self):
        result = analyze_payload(json.dumps([payload(".env", "OPENAI_API_KEY=sk-test")]))[0]
        self.assertEqual(result["status"], "blocked")

    def test_blocks_database_dumps(self):
        result = analyze_payload(json.dumps([payload("dump.sql", "CREATE TABLE users;")]))[0]
        self.assertEqual(result["status"], "blocked")

    def test_accepts_single_payload_object(self):
        result = analyze_payload(json.dumps(payload("note.txt", "hello")))[0]
        self.assertEqual(result["status"], "accepted")

    def test_redacts_text_secret_values(self):
        result = analyze_payload(json.dumps([
            payload("notes.txt", "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456\nhello")
        ]))[0]
        self.assertEqual(result["status"], "accepted")
        self.assertIn("OPENAI_API_KEY=<redacted>", result["summary"])
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", result["summary"])

    def test_accepts_image_without_raw_data(self):
        result = analyze_payload(json.dumps([payload("screen.png", b"\x89PNG", "image/png")]))[0]
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["kind"], "image")
        self.assertNotIn("data_base64", result)

    def test_extracts_docx_text(self):
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr(
                "word/document.xml",
                "<w:document xmlns:w='w'><w:body><w:p><w:r><w:t>Hello doc</w:t></w:r></w:p></w:body></w:document>",
            )
        result = analyze_payload(json.dumps([payload("brief.docx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")]))[0]
        self.assertEqual(result["status"], "accepted")
        self.assertIn("Hello doc", result["summary"])

    def test_zip_is_reference_only(self):
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("README.md", "hello")
            archive.writestr(".env", "SECRET=value")
        result = analyze_payload(json.dumps([payload("project.zip", buf.getvalue(), "application/zip")]))[0]
        self.assertEqual(result["kind"], "zip-reference")
        self.assertIn("README.md", result["summary"])
        self.assertNotIn(".env", result["summary"])


if __name__ == "__main__":
    unittest.main()
