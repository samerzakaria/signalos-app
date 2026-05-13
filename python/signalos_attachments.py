"""Attachment intake for SignalOS chat.

This module accepts file payloads from the desktop UI, classifies them, blocks
secret-like files, and returns redacted summaries only. Raw attachment bytes are
never returned to the frontend or model context.
"""

from __future__ import annotations

import base64
import json
import re
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from signalos_secret_guard import REDACTED, is_secret_path, redact_for_model, redact_text


MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_TEXT_CHARS = 8000

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx"}
TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".log",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".py",
    ".rs",
    ".go",
    ".java",
    ".kt",
    ".swift",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".php",
    ".rb",
    ".sh",
    ".ps1",
    ".bat",
    ".cmd",
    ".html",
    ".css",
    ".scss",
    ".xml",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
}
ZIP_EXTENSIONS = {".zip"}
DATABASE_EXTENSIONS = {".db", ".sqlite", ".sqlite3", ".dump", ".bak", ".sql"}


def analyze_payload(payload_json: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        return [_blocked("attachments", 0, "Attachment payload was not readable.")]

    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        return [_blocked("attachments", 0, "Attachment payload was not a list.")]

    return [analyze_one(item) for item in payload[:10]]


def analyze_one(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        return _blocked("attachment", 0, "Attachment was not readable.")

    name = str(item.get("name") or "attachment")
    size = int(item.get("size") or 0)
    media_type = str(item.get("type") or "")
    suffix = Path(name).suffix.lower()

    if is_secret_path(name) or suffix in DATABASE_EXTENSIONS:
        return _blocked(name, size, "Secret or database files are blocked.")
    if size > MAX_FILE_BYTES:
        return _blocked(name, size, "File is over the 15 MB attachment limit.")

    raw = _decode_base64(str(item.get("data_base64") or ""))
    if raw is None:
        return _blocked(name, size, "Attachment data was not readable.")

    if suffix in IMAGE_EXTENSIONS or media_type.startswith("image/"):
        return _accepted(
            name,
            size,
            "image",
            "Image attached as a local reference. It is not sent to AI text context.",
        )

    if suffix in ZIP_EXTENSIONS:
        return analyze_zip_reference(name, size, raw)

    if suffix in DOCUMENT_EXTENSIONS:
        return analyze_document(name, size, raw, suffix)

    if suffix in TEXT_EXTENSIONS or media_type.startswith("text/"):
        return analyze_text(name, size, raw)

    return _blocked(name, size, "This file type is not supported yet.")


def analyze_text(name: str, size: int, raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8-sig", errors="replace")
    summary = redact_for_model(text[:MAX_TEXT_CHARS], name)
    if not summary.strip():
        summary = "Text file was empty."
    return _accepted(name, size, "text", summary, redacted=summary != text[:MAX_TEXT_CHARS])


def analyze_document(name: str, size: int, raw: bytes, suffix: str) -> dict[str, Any]:
    if suffix == ".docx":
        text = extract_docx_text(raw)
    elif suffix == ".pptx":
        text = extract_pptx_text(raw)
    elif suffix == ".xlsx":
        text = extract_xlsx_text(raw)
    elif suffix == ".pdf":
        text = extract_pdf_text(raw)
    else:
        text = ""

    summary = redact_for_model(text[:MAX_TEXT_CHARS], name).strip()
    if not summary:
        summary = "Document attached. Text extraction did not find readable text."
    return _accepted(name, size, "document", summary, redacted=summary != text[:MAX_TEXT_CHARS].strip())


def analyze_zip_reference(name: str, size: int, raw: bytes) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            names = [
                info.filename
                for info in archive.infolist()
                if not info.is_dir() and not is_secret_path(info.filename)
            ][:20]
    except zipfile.BadZipFile:
        return _blocked(name, size, "Zip file was not readable.")

    listed = "\n".join(f"- {redact_text(item)}" for item in names) or "No readable file names found."
    return _accepted(
        name,
        size,
        "zip-reference",
        "Zip attached as a reference only. Contents were not added to AI context.\n" + listed,
    )


def extract_docx_text(raw: bytes) -> str:
    return _zip_xml_text(raw, ["word/document.xml"])


def extract_pptx_text(raw: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            slide_names = sorted(
                name for name in archive.namelist()
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            )
            return "\n".join(_xml_text(archive.read(name)) for name in slide_names)
    except (OSError, zipfile.BadZipFile, KeyError, ElementTree.ParseError):
        return ""


def extract_xlsx_text(raw: bytes) -> str:
    paths = ["xl/sharedStrings.xml"]
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            paths.extend(
                sorted(
                    name for name in archive.namelist()
                    if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
                )[:8]
            )
            return "\n".join(_xml_text(archive.read(name)) for name in paths if name in archive.namelist())
    except (OSError, zipfile.BadZipFile, KeyError, ElementTree.ParseError):
        return ""


def extract_pdf_text(raw: bytes) -> str:
    text = raw.decode("latin-1", errors="ignore")
    chunks = []
    for match in re.finditer(r"\(([^()]|\\.){2,}\)", text):
        value = match.group(0)[1:-1]
        value = value.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
        if any(ch.isalpha() for ch in value):
            chunks.append(value)
        if len(chunks) >= 200:
            break
    return "\n".join(chunks)


def _zip_xml_text(raw: bytes, paths: list[str]) -> str:
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            return "\n".join(_xml_text(archive.read(path)) for path in paths if path in archive.namelist())
    except (OSError, zipfile.BadZipFile, KeyError, ElementTree.ParseError):
        return ""


def _xml_text(raw: bytes) -> str:
    root = ElementTree.fromstring(raw)
    parts = [node.text.strip() for node in root.iter() if node.text and node.text.strip()]
    return "\n".join(parts)


def _decode_base64(value: str) -> bytes | None:
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        return None


def _accepted(
    name: str,
    size: int,
    kind: str,
    summary: str,
    *,
    redacted: bool = False,
) -> dict[str, Any]:
    return {
        "name": redact_text(name),
        "size": size,
        "kind": kind,
        "status": "accepted",
        "summary": redact_text(summary),
        "redacted": redacted or REDACTED in summary,
    }


def _blocked(name: str, size: int, reason: str) -> dict[str, Any]:
    return {
        "name": redact_text(name),
        "size": size,
        "kind": "blocked",
        "status": "blocked",
        "summary": reason,
        "redacted": True,
    }
