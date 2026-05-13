# SignalOS Core v2.3 — Multi-tenant product namespace support (AMD-CORE-020).
#
# A "product" in SignalOS is a named namespace that isolates sessions,
# journal entries, worktree state, and daemon state under:
#
#   .signalos/products/<id>/sessions/
#   .signalos/products/<id>/journal.jsonl
#   .signalos/products/<id>/worktree-state.json
#   .signalos/products/<id>/daemon.pid
#
# Product ID rules:
#   Slug format: [a-z0-9][a-z0-9-]{0,63}
#   Resolved priority: 1) explicit --product flag  2) SIGNALOS_PRODUCT_ID env
#
# Public API:
#   resolve_product_id(flag_value)        -> str | None
#   validate_product_id(product_id)       -> bool
#   product_root(repo_root, product_id)   -> Path
#   list_products(repo_root)              -> list[str]
#   init_product(repo_root, product_id)   -> Path
#   validate_product(repo_root, product_id) -> dict[str, bool]
#   product_status(repo_root, product_id) -> dict
#   multi_product_summary(repo_root)      -> list[dict]

from __future__ import annotations

__all__ = [
    "resolve_product_id",
    "validate_product_id",
    "product_root",
    "list_products",
    "init_product",
    "validate_product",
    "product_status",
    "multi_product_summary",
    "ProductInitError",
]

import json
import os
import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PRODUCT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_PRODUCTS_DIR = "products"
_SIGNALOS_DIR = ".signalos"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ProductInitError(ValueError):
    """Raised when a product ID is invalid or namespace creation fails."""


# ---------------------------------------------------------------------------
# ID resolution
# ---------------------------------------------------------------------------

def resolve_product_id(flag_value: str | None = None) -> str | None:
    """Return product ID from: 1) explicit flag value, 2) env var, 3) None.

    Never raises — returns None when no product context is set.
    """
    if flag_value is not None:
        return flag_value
    return os.environ.get("SIGNALOS_PRODUCT_ID") or None


def validate_product_id(product_id: str) -> bool:
    """Return True iff product_id matches the slug format [a-z0-9][a-z0-9-]{0,63}."""
    return bool(_PRODUCT_ID_RE.match(product_id))


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def product_root(repo_root: Path, product_id: str) -> Path:
    """Return the product namespace root (may not exist yet).

    >>> product_root(Path("/repo"), "alpha")
    PosixPath('/repo/.signalos/products/alpha')
    """
    return repo_root / _SIGNALOS_DIR / _PRODUCTS_DIR / product_id


def product_sessions_dir(repo_root: Path, product_id: str) -> Path:
    """Return .signalos/products/<id>/sessions/ path."""
    return product_root(repo_root, product_id) / "sessions"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def list_products(repo_root: Path) -> list[str]:
    """Return sorted list of existing product IDs in this repo."""
    products_dir = repo_root / _SIGNALOS_DIR / _PRODUCTS_DIR
    if not products_dir.is_dir():
        return []
    return sorted(
        d.name
        for d in products_dir.iterdir()
        if d.is_dir() and validate_product_id(d.name)
    )


def init_product(repo_root: Path, product_id: str) -> Path:
    """Create the product namespace under .signalos/products/<id>/.

    Creates the sessions/ subdirectory so the namespace is immediately
    usable. Idempotent: calling twice is safe.

    Returns the product root path.
    Raises ProductInitError if product_id is invalid.
    """
    if not validate_product_id(product_id):
        raise ProductInitError(
            f"Invalid product ID {product_id!r}. "
            "Must match [a-z0-9][a-z0-9-]{0,63}."
        )
    proot = product_root(repo_root, product_id)
    (proot / "sessions").mkdir(parents=True, exist_ok=True)
    return proot


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_product(repo_root: Path, product_id: str) -> dict[str, bool]:
    """Check whether Constitution and Soul Document exist for this product.

    Checks product-level namespace first, falls back to repo-level
    governance directory.  This is the wiring-guard Check 10 logic.

    Returns:
        namespace_exists  – .signalos/products/<id>/ is a directory
        constitution      – CONSTITUTION.md found at product or repo level
        soul_document     – SOUL-DOCUMENT.md found at product or repo level
        valid             – both constitution and soul_document are True
    """
    proot = product_root(repo_root, product_id)
    repo_gov = repo_root / "core" / "governance" / "Governance"

    constitution_ok = any(
        p.is_file()
        for p in [proot / "CONSTITUTION.md", repo_gov / "CONSTITUTION.md"]
    )
    soul_doc_ok = any(
        p.is_file()
        for p in [proot / "SOUL-DOCUMENT.md", repo_gov / "SOUL-DOCUMENT.md"]
    )

    return {
        "namespace_exists": proot.is_dir(),
        "constitution": constitution_ok,
        "soul_document": soul_doc_ok,
        "valid": constitution_ok and soul_doc_ok,
    }


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def product_status(repo_root: Path, product_id: str) -> dict[str, Any]:
    """Return a status dict for one product namespace.

    Fields:
        product_id        – the product identifier
        root              – absolute path to the namespace directory
        namespace_exists  – True if the directory exists
        constitution      – True if CONSTITUTION.md found
        soul_document     – True if SOUL-DOCUMENT.md found
        valid             – constitution AND soul_document
        session_count     – number of session subdirs in sessions/
        active_tasks      – tasks not completed/merged in worktree-state.json
    """
    proot = product_root(repo_root, product_id)
    validation = validate_product(repo_root, product_id)

    # Count sessions
    sessions_dir = proot / "sessions"
    session_count = 0
    if sessions_dir.is_dir():
        session_count = sum(1 for d in sessions_dir.iterdir() if d.is_dir())

    # Active task count from product-scoped worktree-state.json
    active_tasks = 0
    worktree_file = proot / "worktree-state.json"
    if worktree_file.is_file():
        try:
            data = json.loads(worktree_file.read_text(encoding="utf-8"))
            active_tasks = sum(
                1
                for t in data.get("worktrees", [])
                if t.get("status") not in {"completed", "merged"}
            )
        except Exception:
            pass

    return {
        "product_id": product_id,
        "root": str(proot),
        "namespace_exists": validation["namespace_exists"],
        "constitution": validation["constitution"],
        "soul_document": validation["soul_document"],
        "valid": validation["valid"],
        "session_count": session_count,
        "active_tasks": active_tasks,
    }


def multi_product_summary(repo_root: Path) -> list[dict[str, Any]]:
    """Return product_status() for every registered product, in sorted order."""
    return [product_status(repo_root, pid) for pid in list_products(repo_root)]
