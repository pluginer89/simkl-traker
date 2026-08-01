"""Entrypoint — dispatches the actions the host and the Settings UI send.

Every branch returns a well-formed envelope (``{"ok": true/false, ...}``); the
host's ``PluginManager`` would wrap an uncaught exception into a generic
``internal_error`` anyway, but catching :class:`SimklApiError` here first gives
callers the specific code (``reauth_required``, ``conflict``, ``not_found``,
``rate_limited``) instead of a flat 500-equivalent.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from . import auth, continue_watching, history, progress
from .client import SimklClient
from .errors import (
    CODE_INVALID_REQUEST,
    CODE_NOT_AUTHENTICATED,
    CODE_NOT_CONFIGURED,
    CODE_REAUTH_REQUIRED,
    CODE_UNSUPPORTED_ACTION,
    SimklApiError,
    err,
    ok,
)
from .util import coerce_float, media_tmdb_id


def _client(context: Dict[str, Any], *, require_token: bool) -> SimklClient:
    secrets = context["secrets"]
    client_id = auth.client_id()

    token = auth.read_token(secrets)
    access_token = token.get("access_token") if token else None
    if require_token and not access_token:
        raise _AuthError("Not connected to Simkl")
    if require_token and token and token.get("reauth_required"):
        raise _ReauthError(str(token.get("reauth_reason") or "reauthorisation required"))

    return SimklClient(http=context["http"], client_id=client_id, access_token=access_token)


class _AuthError(Exception):
    pass


class _ReauthError(Exception):
    pass


def _flag_reauth_on_401(context: Dict[str, Any], exc: SimklApiError) -> None:
    if exc.status == 401:
        auth.flag_reauth(context["secrets"], "simkl_rejected_token")


# ---------------------------------------------------------------------------
# tracker.*
# ---------------------------------------------------------------------------


def _describe() -> Dict[str, Any]:
    return ok({"service": "simkl", "display_name": "Simkl", "auth_kind": "custom"})


def _scrobble(context: Dict[str, Any], payload: Dict[str, Any], action: str) -> Dict[str, Any]:
    media = payload.get("media") or {}
    progress_value = coerce_float(payload.get("progress"))
    client = _client(context, require_token=True)

    body: Dict[str, Any] = {"progress": progress_value}
    if media.get("type") == "movie":
        body["movie"] = {"ids": media.get("ids") or {}}
        if media.get("title"):
            body["movie"]["title"] = media["title"]
        if media.get("year") is not None:
            body["movie"]["year"] = media["year"]
    else:
        show = media.get("show") or {}
        body["show"] = {"ids": show.get("ids") or {}}
        if show.get("title"):
            body["show"]["title"] = show["title"]
        if show.get("year") is not None:
            body["show"]["year"] = show["year"]
        body["episode"] = {"season": media.get("season"), "number": media.get("episode")}

    response = client.post(f"/scrobble/{action}", json=body)
    return ok({"action": action, "response": response})


def _continue_watching(context: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    media_type = str(payload.get("media_type") or "movie")
    limit = int(payload.get("limit") or 20)
    client = _client(context, require_token=True)
    items = continue_watching.build(client, media_type=media_type, limit=limit)
    return ok({"items": items, "count": len(items)})


def _item_progress(context: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    media = payload.get("media") or {}
    tmdb_id = media_tmdb_id(media)
    if tmdb_id is None:
        return err(CODE_INVALID_REQUEST, "media has no tmdb id")

    client = _client(context, require_token=True)
    if media.get("type") == "movie":
        return ok(progress.movie(client, tmdb_id))
    return ok(progress.show(client, tmdb_id))


def _mark_watched(context: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    media = payload.get("media") or {}
    client = _client(context, require_token=True)
    try:
        return ok(history.mark_watched(client, media, watched_at=payload.get("watched_at")))
    except ValueError as exc:
        return err(CODE_INVALID_REQUEST, str(exc))


def _remove_from_continue_watching(context: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    client = _client(context, require_token=True)
    try:
        return ok(history.remove_from_continue_watching(client, playback_id=payload.get("playback_id")))
    except ValueError as exc:
        return err(CODE_INVALID_REQUEST, str(exc))


def _account(context: Dict[str, Any]) -> Dict[str, Any]:
    client = _client(context, require_token=True)
    data = client.get("/sync/user/settings") or {}
    user = data.get("user") or {}
    account = data.get("account") or {}
    return ok(
        {
            "account": {
                "id": account.get("id"),
                "username": user.get("name"),
                # free | pro | vip — Simkl has no expiry field for any tier.
                "plan": account.get("type"),
            }
        }
    )


# ---------------------------------------------------------------------------
# tracker.auth.* — dispatched because plugin.json declares auth.kind="custom"
# ---------------------------------------------------------------------------


def _auth_start(context: Dict[str, Any]) -> Dict[str, Any]:
    try:
        flow = auth.start(
            http=context["http"],
            secrets=context["secrets"],
            cache=context["cache"],
            log=context["log"],
        )
    except ValueError as exc:
        return err(CODE_NOT_CONFIGURED, str(exc))
    return ok(flow)


def _auth_status(context: Dict[str, Any]) -> Dict[str, Any]:
    return ok(
        auth.status(
            secrets=context["secrets"],
            cache=context["cache"],
            http=context["http"],
        )
    )


def _auth_clear(context: Dict[str, Any]) -> Dict[str, Any]:
    auth.clear(context["secrets"], context["cache"])
    return ok({"connected": False, "cleared": True})


# ---------------------------------------------------------------------------
# plugin.* lifecycle + settings
# ---------------------------------------------------------------------------


def _settings_schema(context: Dict[str, Any]) -> Dict[str, Any]:
    # No credentials section: this build's Simkl client id/secret are baked
    # in (see auth.CLIENT_ID/CLIENT_SECRET) — a user only ever connects their
    # own account via the PIN flow below, nothing else to configure.
    return ok(
        {
            "sections": [
                {
                    "id": "account",
                    "title": "Account",
                    "fields": [
                        {
                            "type": "auth_panel",
                            "id": "auth",
                            "label": "Simkl",
                            "help": (
                                "Connect your Simkl account to sync watch history "
                                "and Continue Watching."
                            ),
                        }
                    ],
                },
                {
                    "id": "behaviour",
                    "title": "Behaviour",
                    "fields": [
                        {
                            "type": "action_button",
                            "id": "refresh_widgets",
                            "label": "Refresh Widgets",
                            "style": "secondary",
                            "help": "Reloads the Movies and Shows tabs, including Continue Watching.",
                        },
                        {
                            "type": "info",
                            "id": "note",
                            "text": (
                                "Playback below 80% is saved as Continue Watching; "
                                "80% or higher marks it watched."
                            ),
                        },
                    ],
                },
            ]
        }
    )


def _settings_save(context: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    # Nothing in this plugin's schema is user-editable text/number/toggle
    # anymore — everything is either the auth panel or an action button —
    # so there is never anything to persist here. Kept (rather than removed)
    # so the host's generic "Save" dispatch has somewhere safe to land if a
    # future field is ever added.
    return ok({"saved": []})


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------


def handle(action: str, payload: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    payload = payload or {}
    try:
        if action == "plugin.install" or action in ("plugin.enable", "plugin.disable"):
            return ok({"acknowledged": action})
        if action == "plugin.uninstall":
            return ok({"acknowledged": action})
        if action == "plugin.settings.schema":
            return _settings_schema(context)
        if action == "plugin.settings.save":
            return _settings_save(context, payload)
        if action == "plugin.action.refresh_widgets":
            # Drops this plugin's own cached upstream responses (continue
            # watching, progress); the client-side widget-page reload that
            # follows is what actually re-fetches the Movies/Shows tabs.
            context["cache"].clear()
            return ok({"cleared": True})

        if action == "tracker.describe":
            return _describe()
        if action in ("tracker.scrobble.start", "tracker.scrobble.stop"):
            return _scrobble(context, payload, action.rsplit(".", 1)[-1])
        if action == "tracker.continue_watching":
            return _continue_watching(context, payload)
        if action == "tracker.item_progress":
            return _item_progress(context, payload)
        if action == "tracker.mark_watched":
            return _mark_watched(context, payload)
        if action == "tracker.remove_from_continue_watching":
            return _remove_from_continue_watching(context, payload)
        if action == "tracker.account":
            return _account(context)
        if action == "tracker.cache.clear":
            context["cache"].clear()
            return ok({"cleared": True})

        if action == "tracker.auth.start":
            return _auth_start(context)
        if action in ("tracker.auth.poll", "tracker.auth.status"):
            return _auth_status(context)
        if action == "tracker.auth.clear":
            return _auth_clear(context)

        return err(CODE_UNSUPPORTED_ACTION, f"Unknown action '{action}'")

    except _AuthError as exc:
        return err(CODE_NOT_AUTHENTICATED, str(exc))
    except _ReauthError as exc:
        return err(CODE_REAUTH_REQUIRED, str(exc))
    except SimklApiError as exc:
        _flag_reauth_on_401(context, exc)
        return exc.to_envelope()
