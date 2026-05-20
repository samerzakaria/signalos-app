# SignalOS GitHub OAuth Setup

SignalOS Milestone 4 ("agent ships your work") adds an automatic
`git push origin HEAD` step when QA signs the release gate (G5). If
the workspace has no GitHub remote (or the remote points at a repo
that doesn't exist yet), SignalOS can create the repo on github.com
for you via the GitHub OAuth **device flow** and wire it up as `origin`
before pushing.

The OAuth flow is **opt-in**. If `SIGNALOS_GH_CLIENT_ID` is unset, the
G5 push step skips the auto-repo-creation path and instead records the
push outcome as `deferred` in `.signalos/AUDIT_TRAIL.jsonl` with a
clear reason. Everything else (commit, sign, audit) still works.

## Why the device flow?

The OAuth device flow is the right fit because the CLI/desktop app
doesn't have a stable callback URL. The user opens a GitHub URL on
any device, enters a short code, and the CLI polls for the access
token. This requires **only a client ID** — no client secret, no
redirect URI.

## One-time registration

1. Open https://github.com/settings/developers in your browser.
2. Click **OAuth Apps** -> **New OAuth App**.
3. Fill in:
   - **Application name**: e.g. `SignalOS (local)`
   - **Homepage URL**: e.g. `https://github.com/<you>`
   - **Authorization callback URL**: anything non-empty (e.g.
     `http://localhost`) — the device flow does not use it.
4. On the app's settings page, click **Enable Device Flow**.
5. Copy the **Client ID** (it looks like `Iv1.xxxxxxxxxxxxxxxx`).

## Required OAuth scopes

The auto-repo-creation flow requests the `repo` scope. This covers
both private + public repo creation and `git push` to those repos.
You'll see this scope on the GitHub consent screen when SignalOS
shows you the user code.

## Setting `SIGNALOS_GH_CLIENT_ID`

Export the env var (or put it in your shell rc / `.env` file):

```bash
export SIGNALOS_GH_CLIENT_ID="Iv1.xxxxxxxxxxxxxxxx"
```

On Windows (PowerShell):

```powershell
$env:SIGNALOS_GH_CLIENT_ID = "Iv1.xxxxxxxxxxxxxxxx"
```

SignalOS reads the env var at G5 sign time; restart any long-running
SignalOS process after setting it.

## Verifying the wiring

After signing G5 on a workspace without a remote, check
`.signalos/AUDIT_TRAIL.jsonl` for a `g5-push-result` entry:

- `status: ok` — pushed (or created repo + pushed) successfully.
- `status: deferred` — env var missing, or the user didn't finish
  authorising. Re-run G5 sign after fixing.
- `status: failed` — push hit a hard error; see the `reason` field.

There is **no hardcoded client ID** in the SignalOS source tree.
Auto-repo-creation will not work until you complete the registration
above and set the env var.
