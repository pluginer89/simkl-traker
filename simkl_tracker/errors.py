"""Error mapping between Simkl's HTTP responses and the host's envelope codes.

The host's ``ErrorCode`` constants live in ``warp_mediacenter.backend.plugins.
contracts.common`` — this plugin cannot import that module, so the handful of
strings it needs are duplicated here.  They are part of the host/plugin contract,
not implementation detail, so duplication is the correct boundary: this file
changing does not require touching the host, and vice versa.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

# Mirrors warp_mediacenter.backend.plugins.contracts.common.ErrorCode
CODE_INVALID_REQUEST = "invalid_request"
CODE_UNSUPPORTED_ACTION = "unsupported_action"
CODE_NOT_CONFIGURED = "not_configured"
CODE_NOT_AUTHENTICATED = "not_authenticated"
CODE_REAUTH_REQUIRED = "reauth_required"
CODE_CONFLICT = "conflict"
CODE_RATE_LIMITED = "rate_limited"
CODE_NOT_FOUND = "not_found"
CODE_UPSTREAM_ERROR = "upstream_error"
CODE_INTERNAL_ERROR = "internal_error"


class SimklApiError(Exception):
    """Raised by :mod:`client` for any non-2xx/204 Simkl response."""

    def __init__(
        self,
        status: int,
        *,
        error: Optional[str] = None,
        message: Optional[str] = None,
        body: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.status = status
        self.error = error or ""
        self.message = message or f"Simkl returned HTTP {status}"
        self.body = dict(body or {})
        super().__init__(self.message)

    def to_envelope(self) -> Dict[str, Any]:
        code = _status_to_code(self.status, self.error)
        return {
            "ok": False,
            "error": {
                "code": code,
                "message": self.message,
                "retry_after": None,
                "details": {"status": self.status, "simkl_error": self.error},
            },
        }


def _status_to_code(status: int, error: str) -> str:
    # Branch on the numeric status first — Simkl's own guidance is "branch on
    # `error`, never on `message`", and status is even more stable than `error`.
    if status == 401:
        return CODE_REAUTH_REQUIRED
    if status == 404:
        return CODE_NOT_FOUND
    if status == 409:
        return CODE_CONFLICT
    if status == 412:
        # client_id_failed — the stored client_id is missing or wrong.
        return CODE_NOT_CONFIGURED
    if status == 429:
        return CODE_RATE_LIMITED
    if status == 400:
        # Simkl's scrobble anti-spam guard ("another scrobble write landed within
        # a 20s window") is not a real failure — treat it the same as a duplicate
        # scrobble so the route degrades the same way a Trakt 409 conflict does.
        if error.upper() == "RATE_LIMIT":
            return CODE_CONFLICT
        return CODE_INVALID_REQUEST
    if status >= 500:
        return CODE_UPSTREAM_ERROR
    return CODE_UPSTREAM_ERROR


def ok(data: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    return {"ok": True, "data": dict(data or {})}


def err(code: str, message: str = "", **details: Any) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message or code,
            "retry_after": None,
            "details": details,
        },
    }
