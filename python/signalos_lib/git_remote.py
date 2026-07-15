# SignalOS — GitHub remote helpers for Milestone 4 (audit completion plan).
#
# This module sits between the wave-end git-push flow (sign.py::_auto_push_on_g5)
# and the actual `git push` / GitHub REST calls. Two responsibilities:
#
#   1. ensure_github_remote(root)
#        Look up the configured 'origin' remote URL via `git remote get-url
#        origin`. Returns the URL string, or None if no remote is configured
#        (fresh `git init` workspace).
#
#   2. create_github_repo_via_oauth(repo_name, private=True)
#        Kicks off the GitHub OAuth *device flow* (POST /login/device/code
#        + poll POST /login/oauth/access_token) to obtain a user access
#        token, then calls POST /user/repos to create the repo. Returns
#        the clone URL (https://github.com/<owner>/<repo>.git).
#
#        The device flow needs only a client ID — no client secret, no
#        redirect URI. The user opens a verification URL on any device,
#        enters the displayed user code, and authorises the app; this
#        script polls the token endpoint until the user finishes or the
#        device code expires.
#
# Credential setup — see docs/SIGNALOS_GITHUB_OAUTH_SETUP.md
#
#   The client ID is read from env var SIGNALOS_GH_CLIENT_ID. There is
#   no hardcoded fallback: if the env var is missing,
#   create_github_repo_via_oauth raises RuntimeError so callers can
#   degrade gracefully (the M4 push-on-G5 flow records the push as
#   "deferred" with a clear reason instead of failing the gate).
#
# Stdlib-only by design — we avoid pulling `requests` because nothing
# else in python/ already depends on it. urllib + json gets the job done
# for the four HTTP calls the device flow needs.

from __future__ import annotations

__all__ = [
    "ensure_github_remote",
    "create_github_repo_via_oauth",
    "GITHUB_DEVICE_CODE_URL",
    "GITHUB_ACCESS_TOKEN_URL",
    "GITHUB_REPOS_URL",
]

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .git_process import GitProcessPolicyError, run_git

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# GitHub device-flow endpoints. Documented at:
#   https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#device-flow
GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_REPOS_URL = "https://api.github.com/user/repos"

# Default poll interval / total deadline for the device-flow token poll.
# GitHub returns an `interval` field in the device-code response; we honour
# it (slow-down responses can bump it). The deadline ceiling stops a stale
# auth attempt from leaving the orchestrator hung when the user wanders off.
_DEFAULT_POLL_DEADLINE_SECS = 600  # 10 minutes
_MIN_POLL_INTERVAL_SECS = 5

_HTTP_TIMEOUT_SECS = 30
_USER_AGENT = "signalos-cli"

# Scopes required for "create repo + push code" via the OAuth app.
# `repo` covers private + public repos; the device flow returns the
# granted scopes so we can surface a useful error if the user only
# granted a narrower scope.
_REQUIRED_SCOPE = "repo"


# ---------------------------------------------------------------------------
# Local git helpers
# ---------------------------------------------------------------------------

def ensure_github_remote(workspace_root: Path) -> str | None:
    """Return the configured 'origin' remote URL, or None if there isn't one.

    Uses `git remote get-url origin`. A missing remote, missing .git
    directory, or git-not-installed condition all collapse to None — the
    caller (sign.py::_auto_push_on_g5) treats None as "kick off the
    auto-create OAuth flow".

    Best-effort: any subprocess failure returns None rather than raising.
    Callers shouldn't have their G5 sign blocked by an unusual git state.
    """
    if not (workspace_root / ".git").exists():
        return None
    try:
        proc = run_git(
            ["remote", "get-url", "origin"],
            cwd=workspace_root,
            runner=subprocess.run,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError, GitProcessPolicyError):
        return None
    if proc.returncode != 0:
        return None
    url = (proc.stdout or "").strip()
    return url or None


# ---------------------------------------------------------------------------
# Device-flow HTTP helpers
# ---------------------------------------------------------------------------

def _http_post_form(url: str, params: dict[str, str], *, accept_json: bool = True) -> dict:
    """POST *params* as application/x-www-form-urlencoded and decode JSON."""
    data = urllib.parse.urlencode(params).encode("utf-8")
    headers = {
        "User-Agent": _USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if accept_json:
        headers["Accept"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECS) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(body)
    except ValueError as exc:
        raise RuntimeError(
            f"GitHub device flow: non-JSON response from {url}: {body[:200]!r}"
        ) from exc


def _http_post_json(url: str, payload: dict, token: str) -> dict:
    """POST a JSON payload with a bearer token, decode JSON response."""
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECS) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        # Surface the GitHub error body verbatim — the API returns useful
        # messages like "name already exists on this account".
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(
            f"GitHub API {url} returned HTTP {exc.code}: {detail[:400]}"
        ) from exc
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"GitHub API {url}: non-JSON response: {raw[:200]!r}"
        ) from exc


# ---------------------------------------------------------------------------
# Public OAuth flow
# ---------------------------------------------------------------------------

def create_github_repo_via_oauth(
    repo_name: str,
    private: bool = True,
    *,
    poll_deadline_secs: float | None = None,
    on_user_code: callable | None = None,  # type: ignore[valid-type]
) -> str:
    """Create *repo_name* on GitHub via OAuth device flow. Returns clone URL.

    Steps (per GitHub docs, "Authorizing OAuth apps -- Device flow"):

      1. POST /login/device/code with client_id + scope=repo, get back
         { device_code, user_code, verification_uri, interval, expires_in }
      2. Print the user_code and verification_uri to the operator, then
         poll POST /login/oauth/access_token until the user authorises
         (or the device_code expires).
      3. Use the access token to POST /user/repos with name=<repo_name>
         and private=<private>; return the clone_url from the response.

    Args:
        repo_name: bare repo name (no owner prefix; the authenticated
                   user becomes the owner).
        private:   whether to create the repo as private (default True;
                   safer for unintended-leak-during-onboarding).
        poll_deadline_secs: cap on how long we'll wait for the user to
                            authorise (defaults to _DEFAULT_POLL_DEADLINE_SECS;
                            tests pass a tiny value to keep the suite fast).
        on_user_code: optional callable invoked with the verification URL
                      and user code so callers (CLI / GUI) can render
                      their own prompt instead of relying on stdout.

    Raises:
        RuntimeError: if SIGNALOS_GH_CLIENT_ID is unset, the user denies
                      consent, the device code expires, the granted
                      scopes don't include `repo`, or GitHub's REST call
                      to create the repo fails.
    """
    client_id = os.environ.get("SIGNALOS_GH_CLIENT_ID", "").strip()
    if not client_id:
        # Documented opt-in: callers (sign.py::_auto_push_on_g5) catch
        # this and record a "deferred" push outcome rather than
        # surfacing a stack trace to the user.
        raise RuntimeError(
            "SIGNALOS_GH_CLIENT_ID not configured — set this env var to "
            "enable auto-repo creation. See docs/SIGNALOS_GITHUB_OAUTH_SETUP.md"
        )

    deadline = float(poll_deadline_secs or _DEFAULT_POLL_DEADLINE_SECS)

    # Step 1: ask GitHub for a device + user code pair.
    device_resp = _http_post_form(
        GITHUB_DEVICE_CODE_URL,
        {"client_id": client_id, "scope": _REQUIRED_SCOPE},
    )
    device_code = device_resp.get("device_code")
    user_code = device_resp.get("user_code") or ""
    verification_uri = device_resp.get("verification_uri") or "https://github.com/login/device"
    interval = max(int(device_resp.get("interval") or _MIN_POLL_INTERVAL_SECS), _MIN_POLL_INTERVAL_SECS)
    expires_in = float(device_resp.get("expires_in") or deadline)
    if not device_code or not user_code:
        raise RuntimeError(
            f"GitHub device-flow response missing device_code/user_code: {device_resp}"
        )

    # Surface the code to the operator. CLI mode prints; GUI callers can
    # pass on_user_code to render a clickable button.
    if on_user_code is not None:
        try:
            on_user_code(verification_uri, user_code)
        except Exception:
            pass
    sys.stdout.write(
        f"[signalos][oauth] Authorise SignalOS to push to your GitHub:\n"
        f"  1. Open {verification_uri} in any browser\n"
        f"  2. Enter the code: {user_code}\n"
        f"  3. Approve the 'repo' scope\n"
        f"[signalos][oauth] Waiting for authorisation (expires in "
        f"{int(min(expires_in, deadline))}s)...\n"
    )
    sys.stdout.flush()

    # Step 2: poll for the access token. GitHub returns one of three
    # error states during polling that aren't fatal:
    #   authorization_pending — user hasn't approved yet; keep waiting
    #   slow_down              — back off; raise interval by 5s
    #   expired_token / access_denied — fatal
    started = time.time()
    deadline_ts = started + min(expires_in, deadline)
    access_token: str | None = None
    granted_scopes = ""
    while time.time() < deadline_ts:
        try:
            tok_resp = _http_post_form(
                GITHUB_ACCESS_TOKEN_URL,
                {
                    "client_id": client_id,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
        except urllib.error.URLError as exc:
            # Transient network blip — try again on the next interval.
            sys.stdout.write(f"[signalos][oauth] poll error (retrying): {exc}\n")
            time.sleep(interval)
            continue

        if "access_token" in tok_resp:
            access_token = tok_resp["access_token"]
            granted_scopes = tok_resp.get("scope", "") or ""
            break

        err = tok_resp.get("error")
        if err == "authorization_pending":
            time.sleep(interval)
            continue
        if err == "slow_down":
            interval += 5
            time.sleep(interval)
            continue
        if err in ("expired_token", "access_denied", "unsupported_grant_type",
                   "incorrect_client_credentials", "incorrect_device_code"):
            raise RuntimeError(
                f"GitHub OAuth flow failed: {err}: "
                f"{tok_resp.get('error_description', '(no description)')}"
            )
        # Unknown error shape — abort rather than spin.
        raise RuntimeError(f"GitHub OAuth flow: unexpected response: {tok_resp}")

    if not access_token:
        raise RuntimeError(
            f"GitHub OAuth flow: timed out waiting for user authorisation "
            f"after {int(time.time() - started)}s"
        )

    # Verify the user granted the scope we need before we attempt a
    # write call — gives a clearer error than the API's "Requires
    # authentication" response.
    if _REQUIRED_SCOPE not in granted_scopes.split(","):
        sys.stdout.write(
            f"[signalos][oauth] WARN: granted scopes '{granted_scopes}' may "
            f"not include '{_REQUIRED_SCOPE}'. Continuing — GitHub will "
            f"reject the repo-create call if the scope is insufficient.\n"
        )

    # Step 3: create the repo.
    repo = _http_post_json(
        GITHUB_REPOS_URL,
        {"name": repo_name, "private": bool(private), "auto_init": False},
        access_token,
    )
    clone_url = repo.get("clone_url") or repo.get("html_url")
    if not clone_url:
        raise RuntimeError(
            f"GitHub create-repo succeeded but response had no clone_url: {repo}"
        )
    sys.stdout.write(
        f"[signalos][oauth] Created repo: {repo.get('full_name', repo_name)} "
        f"(private={bool(private)})\n"
    )
    return str(clone_url)
