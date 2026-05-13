# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
# cli/signalos_lib/oidc_provider.py
# W6.3 — OIDC gate signing (AMD-CORE-027)
#
# Provides:
#   OIDCError          — raised on any OIDC auth failure
#   hash_oidc_sub()    — SHA-256 hex of the OIDC subject identifier
#   fetch_oidc_token() — browser-based PKCE OAuth flow; returns identity dict
#   build_oidc_entry() — (oidc_sub_hash, oidc_issuer) tuple for signature blocks
#
# Environment variables:
#   SIGNALOS_OIDC_ISSUER     — OpenID Connect issuer URL (e.g. https://accounts.google.com)
#   SIGNALOS_OIDC_CLIENT_ID  — OAuth 2.0 public client ID registered with the IDP

from __future__ import annotations

__all__ = [
    "OIDCError",
    "hash_oidc_sub",
    "fetch_oidc_token",
    "build_oidc_entry",
    "OIDC_ISSUER_ENV",
    "OIDC_CLIENT_ID_ENV",
]

import base64
import hashlib
import json
import os
import secrets
import urllib.parse
import urllib.request
from typing import Any, Callable

OIDC_ISSUER_ENV = "SIGNALOS_OIDC_ISSUER"
OIDC_CLIENT_ID_ENV = "SIGNALOS_OIDC_CLIENT_ID"

_CALLBACK_PORT = 7749
_CALLBACK_PATH = "/callback"
_DEFAULT_SCOPES = "openid profile email"


# ---------------------------------------------------------------------------
# Public errors
# ---------------------------------------------------------------------------

class OIDCError(Exception):
    """Raised when OIDC authentication or token exchange fails."""


# ---------------------------------------------------------------------------
# Core helpers (fully unit-tested)
# ---------------------------------------------------------------------------

def hash_oidc_sub(sub: str) -> str:
    """Return SHA-256 hex digest of the OIDC subject identifier."""
    return hashlib.sha256(sub.encode("utf-8")).hexdigest()


def _pkce_challenge(verifier: str) -> str:
    """Compute the PKCE S256 code_challenge from a plain-text verifier string."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _get_oidc_config(issuer: str) -> dict:
    """Fetch the OpenID Connect discovery document at /.well-known/openid-configuration."""
    url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:  # type: ignore[arg-type]
            return json.loads(resp.read().decode("utf-8"))
    except OIDCError:
        raise
    except Exception as exc:
        raise OIDCError(f"Failed to fetch OIDC config from {url}: {exc}") from exc


def _exchange_code(
    token_endpoint: str,
    code: str,
    code_verifier: str,
    client_id: str,
    redirect_uri: str,
) -> dict:
    """Exchange an authorization code for tokens at *token_endpoint*."""
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }).encode("utf-8")
    req = urllib.request.Request(token_endpoint, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # type: ignore[arg-type]
            return json.loads(resp.read().decode("utf-8"))
    except OIDCError:
        raise
    except Exception as exc:
        raise OIDCError(f"Token exchange failed: {exc}") from exc


def _decode_jwt_claims(token: str) -> dict:
    """Decode the payload of a JWT without verifying the signature.

    Used only on tokens we just received from the token endpoint, so
    signature verification is handled by the IDP's token endpoint itself.
    """
    parts = token.split(".")
    if len(parts) < 2:
        raise OIDCError("Malformed JWT: fewer than 2 dot-separated parts")
    payload = parts[1]
    # Restore base64url padding
    padding = 4 - len(payload) % 4
    if padding != 4:
        payload += "=" * padding
    try:
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        raise OIDCError(f"Failed to decode JWT payload: {exc}") from exc


# ---------------------------------------------------------------------------
# Browser flow + callback server
# Marked pragma: no cover — injected in all tests via _open_browser /
# _wait_callback kwargs; production callers hit these paths at runtime.
# ---------------------------------------------------------------------------

def _wait_for_callback(port: int, timeout: int = 120) -> dict[str, str]:  # pragma: no cover
    """
    Start a local HTTP server on 127.0.0.1:<port> and block until the
    OIDC redirect callback arrives or *timeout* seconds elapse.

    Returns the query-string parameters from the callback URL.
    """
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Event
    import time

    done: Event = Event()
    result: dict[str, str] = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            result.update(dict(urllib.parse.parse_qsl(parsed.query)))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body>"
                b"<h2>Authentication complete. You may close this tab.</h2>"
                b"</body></html>"
            )
            done.set()

        def log_message(self, fmt: str, *args: Any) -> None:
            pass  # silence HTTP access log

    server = HTTPServer(("127.0.0.1", port), _Handler)
    server.timeout = 1
    deadline = time.monotonic() + timeout
    while not done.is_set() and time.monotonic() < deadline:
        server.handle_request()
    server.server_close()

    if not done.is_set():
        raise OIDCError(
            "OIDC callback timed out — complete the browser sign-in within "
            f"{timeout} seconds"
        )
    return result


def _default_open_browser(url: str) -> None:  # pragma: no cover
    """Open the system default browser to *url*."""
    import webbrowser
    webbrowser.open(url)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_oidc_token(
    issuer: str | None = None,
    client_id: str | None = None,
    *,
    _open_browser: Callable[[str], None] | None = None,
    _wait_callback: Callable[[int, int], dict[str, str]] | None = None,
    _fetch_config: Callable[[str], dict] | None = None,
    _exchange: Callable[..., dict] | None = None,
) -> dict[str, str]:
    """
    Run a browser-based OIDC / PKCE flow and return the authenticated identity.

    Returns a dict with keys:
        oidc_sub_hash  — SHA-256 hex of the ``sub`` claim
        oidc_issuer    — issuer URL
        name           — display name (empty string if IDP does not provide it)
        email          — email claim (empty string if absent)

    Parameters
    ----------
    issuer      OIDC issuer URL, or ``SIGNALOS_OIDC_ISSUER`` env var.
    client_id   OAuth public client ID, or ``SIGNALOS_OIDC_CLIENT_ID`` env var.
    _open_browser    Injected for testing; production default is ``webbrowser.open``.
    _wait_callback   Injected for testing; production default is ``_wait_for_callback``.
    _fetch_config    Injected for testing; production default is ``_get_oidc_config``.
    _exchange        Injected for testing; production default is ``_exchange_code``.
    """
    issuer = issuer or os.environ.get(OIDC_ISSUER_ENV, "")
    client_id = client_id or os.environ.get(OIDC_CLIENT_ID_ENV, "")

    if not issuer:
        raise OIDCError(
            f"OIDC issuer not configured. "
            f"Set {OIDC_ISSUER_ENV} environment variable or pass issuer= argument."
        )
    if not client_id:
        raise OIDCError(
            f"OIDC client ID not configured. "
            f"Set {OIDC_CLIENT_ID_ENV} environment variable or pass client_id= argument."
        )

    _open_browser = _open_browser or _default_open_browser
    _wait_callback = _wait_callback or _wait_for_callback
    _fetch_config = _fetch_config or _get_oidc_config
    _exchange = _exchange or _exchange_code

    # PKCE pair
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = _pkce_challenge(code_verifier)
    state = secrets.token_urlsafe(16)

    # Discover IDP endpoints
    oidc_config = _fetch_config(issuer)
    auth_endpoint = oidc_config.get("authorization_endpoint", "")
    token_endpoint = oidc_config.get("token_endpoint", "")
    if not auth_endpoint:
        raise OIDCError("OIDC discovery document missing 'authorization_endpoint'")
    if not token_endpoint:
        raise OIDCError("OIDC discovery document missing 'token_endpoint'")

    # Build authorisation URL and launch browser
    redirect_uri = f"http://127.0.0.1:{_CALLBACK_PORT}{_CALLBACK_PATH}"
    auth_url = auth_endpoint + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": _DEFAULT_SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    _open_browser(auth_url)

    # Receive the redirect callback
    callback_params = _wait_callback(_CALLBACK_PORT, 120)

    if "error" in callback_params:
        desc = callback_params.get("error_description", "")
        raise OIDCError(
            f"OIDC authorisation error: {callback_params['error']}"
            + (f" — {desc}" if desc else "")
        )
    if callback_params.get("state") != state:
        raise OIDCError("OIDC state mismatch — possible CSRF attack; aborting")
    code = callback_params.get("code")
    if not code:
        raise OIDCError("OIDC callback missing 'code' parameter")

    # Exchange code for ID token
    token_response = _exchange(token_endpoint, code, code_verifier, client_id, redirect_uri)
    id_token = token_response.get("id_token")
    if not id_token:
        raise OIDCError("Token endpoint response missing 'id_token'")

    # Decode claims (no local sig verification — token came straight from the IDP)
    claims = _decode_jwt_claims(id_token)
    sub = claims.get("sub", "")
    if not sub:
        raise OIDCError("id_token claims missing required 'sub' field")

    return {
        "oidc_sub_hash": hash_oidc_sub(sub),
        "oidc_issuer": issuer,
        "name": claims.get("name", ""),
        "email": claims.get("email", ""),
    }


def build_oidc_entry(oidc_result: dict[str, str]) -> tuple[str, str]:
    """
    Extract (oidc_sub_hash, oidc_issuer) from an ``fetch_oidc_token`` result dict.

    These two values are embedded directly into the YAML signature block by
    ``sign_artifact`` when ``--oidc`` is passed to ``signalos sign``.
    """
    return oidc_result["oidc_sub_hash"], oidc_result["oidc_issuer"]
