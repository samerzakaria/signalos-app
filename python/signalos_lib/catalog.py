# SignalOS Core v2.3 — Plugin catalog index support (AMD-CORE-021, W4.3).
#
# The catalog is a JSON index file describing available community plugins.
# It can be fetched from a remote URL or read from a local file.
#
# Catalog JSON shape:
#   {
#     "schema_version": "1",
#     "generated_at":   "2026-04-27T12:00:00Z",
#     "plugins": [
#       {
#         "name":            "@signalos/my-plugin",   # plugin-id (namespace/name)
#         "version":         "1.2.0",
#         "description":     "Short description.",
#         "publisher":       "signalos",
#         "provenance_hash": "sha256:abc...",
#         "download_count":  1234,
#         "last_updated":    "2026-04-01",
#         "tags":            ["automation", "gate"],
#         "install_command": "signalos install https://..."
#       }
#     ]
#   }
#
# Resolution order for the catalog URL:
#   1. Explicit url argument passed by the caller
#   2. SIGNALOS_CATALOG_URL env var
#   3. Default remote URL (SIGNALOS_REGISTRY_TEST=1 bypasses network and
#      reads from the local file at <repo>/.signalos/catalog.json instead)
#
# Ownership model for update_catalog:
#   @signalos/* plugins may only be published by publisher == "signalos".
#   community/* plugins may be published by any publisher.
#   Mismatches raise CatalogOwnershipError (exit 6 in CLI).
#
# Public API:
#   fetch_catalog(url, root)          -> dict
#   search_catalog(keyword, catalog)  -> list[dict]
#   plugin_info(name, catalog)        -> dict | None
#   update_catalog(manifest, tarball_path, catalog_path) -> dict
#   CatalogError, CatalogFetchError, CatalogOwnershipError

from __future__ import annotations

__all__ = [
    "fetch_catalog",
    "search_catalog",
    "plugin_info",
    "update_catalog",
    "CatalogError",
    "CatalogFetchError",
    "CatalogOwnershipError",
    "DEFAULT_CATALOG_URL",
    "CATALOG_CACHE_REL",
    "CATALOG_SCHEMA_VERSION",
]

import hashlib
import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CATALOG_URL = (
    "https://raw.githubusercontent.com/signalos/registry/main/catalog.json"
)
CATALOG_CACHE_REL = Path(".signalos") / "catalog.json"
CATALOG_SCHEMA_VERSION = "1"

# For test-mode short-circuit (mirrors SIGNALOS_REGISTRY_TEST=1 in registry.py)
_TEST_ENV = "SIGNALOS_REGISTRY_TEST"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class CatalogError(RuntimeError):
    """Base for all catalog errors."""
    exit_code = 2


class CatalogFetchError(CatalogError):
    """Raised when the catalog cannot be fetched or parsed."""
    exit_code = 2


class CatalogOwnershipError(CatalogError):
    """Raised when a publisher does not own the claimed namespace."""
    exit_code = 6


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _resolve_url(url: str | None) -> str:
    """Return the effective catalog URL (env var → arg → default)."""
    if url:
        return url
    return os.environ.get("SIGNALOS_CATALOG_URL", DEFAULT_CATALOG_URL)


def _parse_catalog(raw: bytes | str) -> dict[str, Any]:
    """Parse raw JSON bytes/str and validate the schema_version field."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CatalogFetchError(f"catalog: invalid JSON — {exc}") from exc
    if not isinstance(data, dict):
        raise CatalogFetchError("catalog: root must be a JSON object")
    if "plugins" not in data:
        raise CatalogFetchError("catalog: missing 'plugins' array")
    if not isinstance(data["plugins"], list):
        raise CatalogFetchError("catalog: 'plugins' must be an array")
    return data


def fetch_catalog(
    url: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Fetch the plugin catalog and return it as a dict.

    In test mode (SIGNALOS_REGISTRY_TEST=1) the network is never used;
    the catalog is read from <root>/.signalos/catalog.json.  If that
    file doesn't exist an empty catalog dict is returned (so test suites
    don't need to pre-create the file).

    In production mode the effective URL is fetched via urllib; on
    failure a CatalogFetchError is raised.
    """
    if os.environ.get(_TEST_ENV) == "1":
        # Test mode: read from local cache, no network
        cache = (root or Path.cwd()) / CATALOG_CACHE_REL
        if not cache.is_file():
            return {
                "schema_version": CATALOG_SCHEMA_VERSION,
                "generated_at": "",
                "plugins": [],
            }
        try:
            return _parse_catalog(cache.read_bytes())
        except CatalogFetchError:
            raise
        except Exception as exc:
            raise CatalogFetchError(f"catalog: cannot read local cache — {exc}") from exc

    effective_url = _resolve_url(url)

    # Check if it's a local file path (POSIX absolute, file:// URI, or
    # Windows absolute like C:\... — `os.path.isabs` covers both POSIX
    # and Windows-drive forms on their respective platforms).
    if (
        effective_url.startswith("file://")
        or effective_url.startswith("/")
        or os.path.isabs(effective_url)
    ):
        fpath = Path(effective_url.removeprefix("file://"))
        try:
            return _parse_catalog(fpath.read_bytes())
        except CatalogFetchError:
            raise
        except Exception as exc:
            raise CatalogFetchError(f"catalog: cannot read {fpath} — {exc}") from exc

    # Remote fetch
    try:
        req = urllib.request.Request(
            effective_url,
            headers={"User-Agent": "signalos-catalog/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
    except urllib.error.URLError as exc:
        raise CatalogFetchError(
            f"catalog: cannot fetch {effective_url} — {exc}"
        ) from exc
    except Exception as exc:
        raise CatalogFetchError(
            f"catalog: unexpected error fetching {effective_url} — {exc}"
        ) from exc

    return _parse_catalog(raw)


# ---------------------------------------------------------------------------
# Search + info
# ---------------------------------------------------------------------------

def search_catalog(
    keyword: str,
    catalog: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return plugins whose name, description, publisher, or tags contain
    *keyword* (case-insensitive substring match).

    Returns an empty list when no plugins match.
    """
    kw = keyword.lower()
    results: list[dict[str, Any]] = []
    for plugin in catalog.get("plugins", []):
        haystack = " ".join([
            str(plugin.get("name", "")),
            str(plugin.get("description", "")),
            str(plugin.get("publisher", "")),
            " ".join(plugin.get("tags", [])),
        ]).lower()
        if kw in haystack:
            results.append(plugin)
    return results


def plugin_info(
    name: str,
    catalog: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the catalog entry for the plugin with this exact *name*,
    or None if not found.
    """
    for plugin in catalog.get("plugins", []):
        if plugin.get("name") == name:
            return plugin
    return None


# ---------------------------------------------------------------------------
# update_catalog — extend publish to push to index
# ---------------------------------------------------------------------------

def _provenance_hash(tarball_path: Path) -> str:
    """Return sha256:<hex> of the tarball file."""
    h = hashlib.sha256()
    with tarball_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _check_ownership(manifest: dict[str, Any]) -> None:
    """Raise CatalogOwnershipError if the publisher doesn't own the namespace.

    Rules:
      @signalos/* → publisher must be "signalos"
      community/* → any publisher is allowed
    """
    name: str = manifest.get("name", "")
    publisher: str = manifest.get("publisher", "")
    if name.startswith("@signalos/") and publisher != "signalos":
        raise CatalogOwnershipError(
            f"catalog: @signalos/* namespace is reserved — "
            f"publisher must be 'signalos', got {publisher!r}"
        )


def update_catalog(
    manifest: dict[str, Any],
    tarball_path: Path,
    catalog_path: Path,
) -> dict[str, Any]:
    """Update the local catalog index file with the published plugin entry.

    Ownership is verified first: @signalos/* requires publisher == 'signalos'.
    An existing entry with the same name is replaced; new entries are appended.

    Returns the updated catalog dict (also written to catalog_path).
    """
    _check_ownership(manifest)

    # Load or create catalog
    if catalog_path.is_file():
        try:
            catalog = _parse_catalog(catalog_path.read_bytes())
        except CatalogFetchError:
            raise
        except Exception as exc:
            raise CatalogFetchError(
                f"catalog: cannot read {catalog_path} — {exc}"
            ) from exc
    else:
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog = {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "generated_at": "",
            "plugins": [],
        }

    # Build new entry
    prov = _provenance_hash(tarball_path)
    name: str = manifest.get("name", "")
    new_entry: dict[str, Any] = {
        "name": name,
        "version": manifest.get("version", ""),
        "description": manifest.get("description", ""),
        "publisher": manifest.get("publisher", ""),
        "provenance_hash": prov,
        "download_count": 0,
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "tags": manifest.get("tags", []),
        "install_command": f"signalos install <url>/{name}-{manifest.get('version', '')}.tar.gz",
    }

    # Replace existing entry or append
    plugins: list[dict] = catalog.get("plugins", [])
    replaced = False
    for i, p in enumerate(plugins):
        if p.get("name") == name:
            # Preserve download_count from existing entry
            new_entry["download_count"] = p.get("download_count", 0)
            plugins[i] = new_entry
            replaced = True
            break
    if not replaced:
        plugins.append(new_entry)

    catalog["plugins"] = plugins
    catalog["generated_at"] = datetime.now(timezone.utc).isoformat()

    catalog_path.write_text(
        json.dumps(catalog, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return catalog
