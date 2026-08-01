# Simkl Tracker Plugin

A Tracker plugin for Warp MediaCenter's plugin host. Scrobbling, Continue
Watching, per-item progress, and mark-watched via [Simkl](https://simkl.com).

Lives here — outside `warp_mediacenter/` — so it can be installed, removed and
iterated on independently of the host build, exactly like a third-party plugin
would be.

## 1. Client ID / Secret — nothing to do

This plugin's Simkl developer-portal app (client id + secret) is baked into
`auth.py` — every install of this plugin build shares it, so there is no "go
create your own Simkl app" step for anyone installing it. Only the per-user
access token, obtained via the PIN flow below, is user-specific, and it is
the only thing that ever lands in the `plugin_secrets` table — the client
id/secret never touch the database.

## 2. Install it — live, through the running app

Backend must be running (`python -m warp_mediacenter.cli.media serve`).

**Via the app (the real flow):** the file browser in Settings → Plugins only
lists directories (for navigating) and `.zip` files (for selecting) — a bare
`plugin.json` or source folder is never itself a valid selection, by design.
So build the package first:

```bash
cd /Users/k2_mac/Documents/Workspace/Experiments/warp-mediacenter
python3 -c "
import zipfile
from pathlib import Path
src = Path('simkl-tracker')
with zipfile.ZipFile('simkl-tracker.zip', 'w', zipfile.ZIP_DEFLATED) as zf:
    for p in sorted(src.rglob('*')):
        if p.is_file() and p.name != 'README.md' and '__pycache__' not in p.parts:
            zf.write(p, p.relative_to(src))
"
```

This produces `simkl-tracker.zip` **at the repo root**, next to the
`simkl-tracker/` source folder — with `plugin.json` sitting at the zip's own
root (not nested inside a `simkl-tracker/` entry), which is what the manifest
discovery expects. Re-run this after every edit to the plugin's source; the
zip is a build artifact, not something to hand-maintain.

Then: Settings → Plugins → Trackers → **Install new Plugin** → the folder
browser opens on the backend filesystem → navigate to the repo root
(`warp-mediacenter/`, *not* into the `simkl-tracker/` folder) → select
`simkl-tracker.zip`. It appears as a row under Trackers with a switch.

**Via curl**, if you want to watch it land without opening the app first (the
backend's `PluginManager.install()` also accepts a directory path directly,
which is why this curl example points at the folder rather than the zip — no
packaging step needed for this path):

```bash
curl -X POST http://localhost:8000/api/v1/plugins/install \
  -H 'Content-Type: application/json' \
  -d '{"source": "/Users/k2_mac/Documents/Workspace/Experiments/warp-mediacenter/simkl-tracker"}'
```

**Via the CLI:**

```bash
python -m warp_mediacenter.cli.admin plugins install ./simkl-tracker
```

Any of these land the plugin under `var/plugins/simkl-tracker/<version>/` and
register it in `plugin_state` — the source folder here is untouched; installing
again after an edit re-copies it.

## 3. Enable it

Trackers are exclusive — enabling Simkl disables whatever tracker (built-in
Trakt, or nothing) was active before.

```bash
curl -X POST http://localhost:8000/api/v1/plugins/simkl-tracker/enable
```

Or the switch in Settings → Plugins → Trackers.

## 4. Connect your Simkl account

In the app: Settings → Plugins → **Simkl** (its own sidebar entry, appears once
installed) → **Connect Simkl** → a code + `simkl.com/pin` link appear → enter
the code there → the panel flips to Connected within a few seconds, showing
the connected username and Simkl plan tier (free/pro/vip).

Via curl, the same two calls the app makes:

```bash
BASE=http://localhost:8000/api/v1/plugins/simkl-tracker

curl -X POST $BASE/auth/start
# -> {"status": "pending", "user_code": "AB12C", "verification_url": "https://simkl.com/pin", ...}
# visit the URL, enter the code, then poll:

curl -X POST $BASE/auth/poll
# -> {"connected": true, "username": "...", "plan": "free|pro|vip", ...} once approved
```

The settings page also has a **Refresh Widgets** button — it clears this
plugin's own cached upstream responses and reloads both the Movies and Shows
tabs client-side, the same "Refresh Widgets" the Catalog configuration panel
already has.

## 5. Watch it work

```bash
# Continue Watching now comes from Simkl instead of the empty/legacy path
curl "http://localhost:8000/api/v1/catalog/tracker/continue_watching?media_type=show"

# Play something in the app — a real scrobble reaches Simkl
curl -X POST http://localhost:8000/api/v1/player/scrobble/start -H 'Content-Type: application/json' \
  -d '{"media_type":"movie","progress":5,"media":{"title":"Some Movie","ids":{"tmdb":27205}}}'
```

Or just use the app normally — install, enable, connect, then play something.
The Continue Watching row and the health endpoint (`/api/v1/health` →
`subsystems.plugins.tracker`) both reflect it live.

## Uninstalling / switching back

```bash
curl -X DELETE "http://localhost:8000/api/v1/plugins/simkl-tracker?force=true"
```

Drops its files, its `plugin_secrets` rows, and its registry entry. The app
falls back to whatever tracker (built-in Trakt, or none) was previously
active — nothing else changes.

## Notes on the Simkl API shape

- Every request carries `client_id` **as a query parameter**, not just in the
  auth header — this plugin's `client.py` merges it in on every call.
- The sign-in flow is `GET /oauth/pin` + `GET /oauth/pin/{user_code}` (not
  Trakt's `POST` + JSON body), which is different enough from the host's
  generic device-code assumption that this plugin declares
  `"auth": {"kind": "custom"}` and runs its own PIN flow in `auth.py`, using
  the host's HTTP client and secrets store — no host changes were needed for
  that.
- Access tokens are long-lived (~5 years) with no refresh token and no refresh
  endpoint — `auth.py` has no refresh logic to write, only a `reauth_required`
  flag set if Simkl ever rejects the token with a 401.
- Continue Watching for shows is a merge of `GET /sync/playback/episodes`
  (paused mid-episode) and `GET /sync/all-items/shows/watching?next_watch_info=yes`
  (episode counts + next-unwatched hint) — see `continue_watching.py` for the
  resume-point priority rules.
- Items without a resolvable TMDb id are skipped rather than crashing the row —
  Simkl's catalog is more IMDB/TVDB-centric for shows than Trakt's, so not
  every item is guaranteed to carry one.
- Account/profile info comes from `GET /sync/user/settings` (the current
  endpoint per api.simkl.org's live-verified docs — the older apiary docs
  describe a `POST /users/settings` that no longer appears in the current
  spec). `account.type` is `free | pro | vip` — Simkl has no expiry/renewal
  date for any tier, so there is nothing to show there; `auth.status()`
  caches this for 10 minutes (`ACCOUNT_CACHE_TTL`) since it's the one
  best-effort network call inside an otherwise local, frequently-polled
  status check.
