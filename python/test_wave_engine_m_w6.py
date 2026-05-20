"""test_wave_engine_m_w6.py — M-W6 translator-mode for inspect-first
fast-forward.

Per WAVE-ENGINE-DESIGN §7 and §13.Q4 resolution.

The translator's optional dependencies (pypdf, python-docx) may not be
installed in the test environment. Tests for those formats assert the
graceful "supported=False with install_hint" path that ships when the
dep is missing, OR use unittest.mock to substitute a fake module so the
extraction path itself is exercised.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from signalos_lib.translator import ExternalFormat, detect_format, translate
from signalos_lib.wave_engine import WaveEngine


# ---------------------------------------------------------------------------
# detect_format
# ---------------------------------------------------------------------------

class DetectFormatTests(unittest.TestCase):
    def test_markdown_by_suffix(self):
        self.assertEqual(detect_format("notes/belief.md"), ExternalFormat.MARKDOWN)
        self.assertEqual(detect_format("BELIEF.MARKDOWN"), ExternalFormat.MARKDOWN)

    def test_pdf_by_suffix(self):
        self.assertEqual(detect_format("brief.pdf"), ExternalFormat.PDF)

    def test_docx_by_suffix(self):
        self.assertEqual(detect_format("requirements.docx"), ExternalFormat.DOCX)

    def test_figma_url(self):
        self.assertEqual(
            detect_format("https://www.figma.com/design/ABC123/My-Design"),
            ExternalFormat.FIGMA,
        )

    def test_generic_url(self):
        self.assertEqual(detect_format("https://example.com/notes"), ExternalFormat.URL)

    def test_remote_pdf_is_url_not_pdf(self):
        """A URL ending in .pdf is still a URL — we don't auto-fetch."""
        self.assertEqual(
            detect_format("https://example.com/brief.pdf"), ExternalFormat.URL,
        )

    def test_empty_input_is_unknown(self):
        self.assertEqual(detect_format(""), ExternalFormat.UNKNOWN)
        self.assertEqual(detect_format("random text"), ExternalFormat.UNKNOWN)


# ---------------------------------------------------------------------------
# translate — markdown
# ---------------------------------------------------------------------------

class TranslateMarkdownTests(unittest.TestCase):
    def test_reads_markdown_content(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "belief.md"
            path.write_text("# Belief\nWe think users want X.\n", encoding="utf-8")
            result = translate(str(path))
        self.assertTrue(result["supported"])
        self.assertEqual(result["format"], ExternalFormat.MARKDOWN)
        self.assertIn("users want X", result["text"])
        self.assertFalse(result["truncated"])

    def test_truncates_at_max_chars(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "belief.md"
            path.write_text("x" * 50_000, encoding="utf-8")
            result = translate(str(path), max_chars=100)
        self.assertTrue(result["supported"])
        self.assertEqual(len(result["text"]), 100)
        self.assertTrue(result["truncated"])

    def test_missing_file_returns_unsupported_with_error(self):
        result = translate("nonexistent.md")
        self.assertFalse(result["supported"])
        self.assertIn("not found", result["error"].lower())


# ---------------------------------------------------------------------------
# translate — URL formats (no extraction, just record)
# ---------------------------------------------------------------------------

class TranslateUrlTests(unittest.TestCase):
    def test_figma_url_records_file_key(self):
        result = translate("https://www.figma.com/design/ABC123XYZ/Spec")
        self.assertTrue(result["supported"])
        self.assertEqual(result["format"], ExternalFormat.FIGMA)
        self.assertEqual(result["figma_file_key"], "ABC123XYZ")
        # Body text is empty — Figma is recorded as a reference.
        self.assertEqual(result["text"], "")
        self.assertIn("design reference", result["note"])

    def test_generic_url_recorded_without_fetch(self):
        result = translate("https://example.com/brief.html")
        self.assertTrue(result["supported"])
        self.assertEqual(result["format"], ExternalFormat.URL)
        self.assertEqual(result["text"], "")
        self.assertIn("does not fetch", result["note"])


# ---------------------------------------------------------------------------
# translate — PDF / DOCX (optional deps)
# ---------------------------------------------------------------------------

class TranslatePdfTests(unittest.TestCase):
    def test_pdf_missing_pypdf_returns_install_hint(self):
        # The test environment doesn't have pypdf installed (M-W6 ships
        # without forcing the dep). Ensure the no-dep path is graceful.
        if "pypdf" in sys.modules:
            self.skipTest("pypdf is installed; graceful-fallback path not exercised here")
        with tempfile.TemporaryDirectory() as d:
            pdf_path = Path(d) / "brief.pdf"
            pdf_path.write_bytes(b"%PDF-1.0\n%%EOF\n")
            result = translate(str(pdf_path))
        self.assertFalse(result["supported"])
        self.assertEqual(result["format"], ExternalFormat.PDF)
        self.assertIn("pypdf", result["install_hint"])

    def test_pdf_with_mocked_pypdf_extracts_text(self):
        """Substitute a fake pypdf module so the extraction path runs
        even when the real dep isn't installed."""
        fake_pages = [
            mock.Mock(extract_text=lambda: "Page one text. "),
            mock.Mock(extract_text=lambda: "Page two text."),
        ]
        fake_reader = mock.Mock(pages=fake_pages)
        fake_pypdf = mock.Mock(PdfReader=mock.Mock(return_value=fake_reader))

        with tempfile.TemporaryDirectory() as d:
            pdf_path = Path(d) / "brief.pdf"
            pdf_path.write_bytes(b"%PDF-1.0\n%%EOF\n")
            with mock.patch.dict(sys.modules, {"pypdf": fake_pypdf}):
                result = translate(str(pdf_path))
        self.assertTrue(result["supported"])
        self.assertEqual(result["format"], ExternalFormat.PDF)
        self.assertIn("Page one text", result["text"])
        self.assertIn("Page two text", result["text"])
        self.assertEqual(result["page_count"], 2)


class TranslateDocxTests(unittest.TestCase):
    def test_docx_missing_dep_returns_install_hint(self):
        if "docx" in sys.modules:
            self.skipTest("python-docx is installed; graceful-fallback path not exercised here")
        with tempfile.TemporaryDirectory() as d:
            docx_path = Path(d) / "requirements.docx"
            docx_path.write_bytes(b"PK\x03\x04stub")  # not a real docx, doesn't matter
            result = translate(str(docx_path))
        self.assertFalse(result["supported"])
        self.assertEqual(result["format"], ExternalFormat.DOCX)
        self.assertIn("python-docx", result["install_hint"])

    def test_docx_with_mocked_python_docx_extracts_paragraphs(self):
        fake_paragraphs = [
            mock.Mock(text="Heading"),
            mock.Mock(text="Body line one."),
            mock.Mock(text=""),  # empty para — should be filtered out
            mock.Mock(text="Body line two."),
        ]
        fake_doc = mock.Mock(paragraphs=fake_paragraphs)
        fake_docx_module = mock.Mock(Document=mock.Mock(return_value=fake_doc))

        with tempfile.TemporaryDirectory() as d:
            docx_path = Path(d) / "requirements.docx"
            docx_path.write_bytes(b"PK\x03\x04stub")
            with mock.patch.dict(sys.modules, {"docx": fake_docx_module}):
                result = translate(str(docx_path))
        self.assertTrue(result["supported"])
        self.assertEqual(result["format"], ExternalFormat.DOCX)
        self.assertIn("Heading", result["text"])
        self.assertIn("Body line one", result["text"])
        self.assertNotIn("\n\n", result["text"])  # empty paragraph filtered
        self.assertEqual(result["paragraph_count"], 3)


# ---------------------------------------------------------------------------
# WaveEngine.translate_external integration
# ---------------------------------------------------------------------------

class EngineTranslateExternalTests(unittest.TestCase):
    def test_engine_translates_markdown_artifact(self):
        root = Path(tempfile.mkdtemp(prefix="signalos-m-w6-engine-")).resolve()
        (root / ".signalos").mkdir()
        belief = root / "belief-from-user.md"
        belief.write_text("Belief: customers need faster onboarding.\n", encoding="utf-8")
        eng = WaveEngine(root)
        result = eng.translate_external(str(belief), gate="G1")
        self.assertTrue(result["translation"]["supported"])
        self.assertEqual(result["gate"], "G1")
        self.assertIn("customers need", result["translation"]["text"])
        # System bubble names the gate and the translator action.
        self.assertEqual(result["system_bubble"]["gate"], "G1")
        self.assertIn("Belief", result["system_bubble"]["text"])

    def test_engine_translate_unsupported_format_returns_install_bubble(self):
        root = Path(tempfile.mkdtemp(prefix="signalos-m-w6-engine-")).resolve()
        (root / ".signalos").mkdir()
        eng = WaveEngine(root)
        result = eng.translate_external("brief.unknown-format", gate="G0")
        self.assertFalse(result["translation"]["supported"])
        self.assertIn("Cannot translate", result["system_bubble"]["detail"])


if __name__ == "__main__":
    unittest.main()
