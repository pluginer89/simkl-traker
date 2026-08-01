"""Simkl's PIN-code sign-in flow.

Simkl's device flow is GET + query params for both the initial request and the
poll, keyed by ``user_code`` rather than the ``device_code`` the response
also carries (Simkl documents ``device_code`` as a fixed placeholder — it plays
no part in polling). That is different enough from the generic POST/JSON
device-code shape the host runs on plugins' behalf that this plugin declares
``"auth": {"kind": "custom"}`` and drives the whole flow itself here, using the
host's HTTP client and secrets store — nothing else.

Token storage: Simkl access tokens are long-lived (~5 years, no refresh token,
no refresh endpoint), so there is no refresh logic to write — only "do we have a
token" and "has the server rejected it" (flagged on a 401).

Flow state (the in-progress PIN/user_code/verification_url) is kept in the
host-provided per-plugin cache with a TTL comfortably longer than Simkl's own
900-second code lifetime, so it survives across the several dozen calls the poll
thread and the Flutter poll UI both make while a login is in flight.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from .client import SimklClient
from .errors import SimklApiError

TOKEN_KEY = "oauth_token"

# This plugin's Simkl developer-portal app — every install of this plugin
# shares it, so it is baked into the build rather than something each user
# has to go create their own app and paste in. Only the per-user access
# token, obtained via the PIN flow below, is user-specific and lives in
# `secrets`; the client id/secret never touch the database.
CLIENT_ID = "b7b323eda1146cf9bc0b3b062e8183d38766484e1b96d553a73621490f5c154c"
CLIENT_SECRET = "4d70f3da2169f775620f14531a81a4afa79aa49653fab7a77977c2104031fd7c"

FLOW_CACHE_KEY = "auth_flow"
FLOW_CACHE_TTL = 1800.0  # comfortably longer than Simkl's 900s code lifetime

# Simkl's /sync/user/settings has no expiry field to show (VIP tiers on Simkl
# don't expire the way a subscription would) — just profile + plan tier. Cache
# it briefly so opening the settings page repeatedly, or the auth-status poll
# ticking during an in-flight login, doesn't hammer the endpoint.
ACCOUNT_CACHE_KEY = "account_info"
ACCOUNT_CACHE_TTL = 600.0

_MIN_POLL_INTERVAL = 5
_PENDING_MESSAGES = {"authorization pending", "authorization_pending"}


def client_id() -> str:
    return CLIENT_ID


def client_secret() -> str:
    return CLIENT_SECRET


def _empty_flow() -> Dict[str, Any]:
    return {
        "status": "none",
        "error": None,
        "user_code": None,
        "verification_url": None,
        "expires_at": None,
        "interval": _MIN_POLL_INTERVAL,
    }


def _read_flow(cache: Any) -> Dict[str, Any]:
    return cache.get(FLOW_CACHE_KEY, FLOW_CACHE_TTL) or _empty_flow()


def _write_flow(cache: Any, flow: Dict[str, Any]) -> None:
    cache.set(FLOW_CACHE_KEY, flow)


def read_token(secrets: Any) -> Optional[Dict[str, Any]]:
    return secrets.get_json(TOKEN_KEY)


def _write_token(secrets: Any, *, access_token: str) -> Dict[str, Any]:
    record = {
        "access_token": access_token,
        "created_at": time.time(),
        "reauth_required": False,
        "reauth_reason": None,
    }
    secrets.set_json(TOKEN_KEY, record)
    return record


def flag_reauth(secrets: Any, reason: str) -> None:
    record = read_token(secrets) or {"access_token": None, "created_at": None}
    record["reauth_required"] = True
    record["reauth_reason"] = reason
    secrets.set_json(TOKEN_KEY, record)


def clear(secrets: Any, cache: Any) -> None:
    secrets.delete(TOKEN_KEY)
    cache.delete(ACCOUNT_CACHE_KEY)
    _write_flow(cache, _empty_flow())


def _account_summary(http: Any, cache: Any, access_token: str) -> Optional[Dict[str, Any]]:
    """Best-effort profile + plan tier for the connected account, cached.

    Simkl's /sync/user/settings has no expiry field — VIP/pro on Simkl is a
    plan tier, not a subscription with an expiry date, so there is nothing to
    show there. A failed fetch (network hiccup, rate limit) must not break the
    connected state itself — it just means the settings page shows the
    checkmark without the extra detail this call adds.
    """

    cached = cache.get(ACCOUNT_CACHE_KEY, ACCOUNT_CACHE_TTL)
    if cached is not None:
        return cached
    try:
        client = SimklClient(http=http, client_id=CLIENT_ID, access_token=access_token)
        data = client.get("/sync/user/settings") or {}
    except Exception:  # noqa: BLE001 - best-effort account detail, never fatal
        return None
    user = data.get("user") or {}
    account = data.get("account") or {}
    summary = {"username": user.get("name"), "plan": account.get("type")}
    cache.set(ACCOUNT_CACHE_KEY, summary)
    return summary


def status(*, secrets: Any, cache: Any, http: Any) -> Dict[str, Any]:
    """Full auth state. Everything but the account summary is a local read;
    that one best-effort network call is short-cached — see
    _account_summary — so this stays cheap even polled every few seconds
    during an in-flight login.
    """

    flow = _read_flow(cache)
    record = read_token(secrets)
    if not record or not record.get("access_token"):
        return {
            "connected": False,
            "configured": True,
            "status": flow.get("status") or "disconnected",
            "flow": flow,
        }

    reauth = bool(record.get("reauth_required"))
    result: Dict[str, Any] = {
        "connected": not reauth,
        "configured": True,
        "status": "reauth_required" if reauth else "connected",
        "reauth_required": reauth,
        "reauth_reason": record.get("reauth_reason"),
        "flow": flow,
    }
    if not reauth:
        summary = _account_summary(http, cache, str(record["access_token"]))
        if summary:
            username = summary.get("username")
            plan = summary.get("plan")
            result["username"] = username
            result["plan"] = plan
            if username:
                result["detail"] = f"@{username}"
    return result


def start(*, http: Any, secrets: Any, cache: Any, log: Any) -> Dict[str, Any]:
    """Request a PIN and begin polling in the background.

    Returns the flat flow record (``PluginAuthFlow`` shape on the Flutter side) —
    not the full status — matching what the settings-page auth panel expects
    from a freshly started flow.
    """

    cid = CLIENT_ID
    client = SimklClient(http=http, client_id=cid, access_token=None)
    data = client.get("/oauth/pin", require_auth=False) or {}

    user_code = str(data.get("user_code") or "")
    if not user_code:
        raise RuntimeError("Simkl did not return a user_code")

    try:
        expires_in = int(data.get("expires_in") or 900)
    except (TypeError, ValueError):
        expires_in = 900
    try:
        interval = max(_MIN_POLL_INTERVAL, int(data.get("interval") or _MIN_POLL_INTERVAL))
    except (TypeError, ValueError):
        interval = _MIN_POLL_INTERVAL

    flow = {
        "status": "pending",
        "error": None,
        "user_code": user_code,
        "verification_url": str(
            data.get("verification_uri") or data.get("verification_url") or "https://simkl.com/pin"
        ),
        "expires_at": time.time() + expires_in,
        "interval": interval,
    }
    _write_flow(cache, flow)

    thread = threading.Thread(
        target=_poll_loop,
        args=(http, secrets, cache, log, cid, user_code, expires_in, interval),
        daemon=True,
        name="simkl-auth-poll",
    )
    thread.start()

    log.info("simkl_auth_pin_issued", user_code=user_code, expires_in=expires_in)
    return dict(flow)


def _poll_loop(
    http: Any,
    secrets: Any,
    cache: Any,
    log: Any,
    client_id: str,
    user_code: str,
    expires_in: int,
    interval: int,
) -> None:
    deadline = time.time() + expires_in
    client = SimklClient(http=http, client_id=client_id, access_token=None)
    polls = 0

    while time.time() < deadline:
        # A newer flow (user hit "Connect" again) replaced this one — stop.
        current = _read_flow(cache)
        if current.get("user_code") != user_code:
            return

        time.sleep(interval)
        polls += 1

        try:
            data = client.get(f"/oauth/pin/{user_code}", require_auth=False, allowed_statuses=(200,)) or {}
        except SimklApiError as exc:
            log.warning("simkl_auth_poll_error", poll=polls, status=exc.status, error=exc.error)
            continue
        except Exception as exc:  # noqa: BLE001 - a poll failure must not kill the thread
            log.warning("simkl_auth_poll_error", poll=polls, error=str(exc))
            continue

        if data.get("result") == "OK" and data.get("access_token"):
            _write_token(secrets, access_token=str(data["access_token"]))
            current = _read_flow(cache)
            if current.get("user_code") == user_code:
                current["status"] = "authorized"
                current["error"] = None
                _write_flow(cache, current)
            log.info("simkl_auth_authorized", polls=polls)
            return

        # A response that looks like a fresh /oauth/pin payload (carries its own
        # device_code) means our code is gone — Simkl's documented signal for
        # "expired or unknown user_code". Nothing else is distinguishable as
        # "denied" in this API, so expiry is the only terminal failure state.
        if data.get("device_code") and data.get("user_code") != user_code:
            current = _read_flow(cache)
            if current.get("user_code") == user_code:
                current["status"] = "expired"
                current["error"] = "The code expired before it was used."
                _write_flow(cache, current)
            log.info("simkl_auth_expired", polls=polls)
            return

        # Otherwise: still pending ("result": "KO", "message": "Authorization
        # pending") — keep polling at the declared interval.

    current = _read_flow(cache)
    if current.get("user_code") == user_code:
        current["status"] = "expired"
        current["error"] = "Polling deadline exceeded"
        _write_flow(cache, current)
    log.warning("simkl_auth_deadline", polls=polls)


__all__ = [
    "CLIENT_ID",
    "CLIENT_SECRET",
    "TOKEN_KEY",
    "client_id",
    "client_secret",
    "clear",
    "flag_reauth",
    "read_token",
    "start",
    "status",
]
