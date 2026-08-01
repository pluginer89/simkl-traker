"""Thin Simkl API client built on top of the host's ``PluginHttpClient``.

Simkl puts ``client_id``/``app-name``/``app-version`` on the query string of
*every* request — including the ones that also carry a Bearer token — so those
three params are merged in here rather than left to each call site to repeat.

The host's HTTP client already handles the allowlist, rate limiting and retries;
this module only adds Simkl's specific request shape and turns non-2xx responses
into :class:`SimklApiError`.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .errors import SimklApiError

APP_NAME = "warp-mediacenter"
APP_VERSION = "1.0"
BASE_URL = "https://api.simkl.com"


class SimklClient:
    def __init__(self, *, http: Any, client_id: str, access_token: Optional[str]) -> None:
        self._http = http
        self._client_id = client_id
        self._access_token = access_token

    @property
    def authenticated(self) -> bool:
        return bool(self._access_token)

    def _params(self, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "client_id": self._client_id,
            "app-name": APP_NAME,
            "app-version": APP_VERSION,
        }
        if extra:
            params.update({k: v for k, v in extra.items() if v is not None})
        return params

    def _headers(self, *, require_auth: bool) -> Dict[str, str]:
        if not self._access_token:
            if require_auth:
                raise SimklApiError(401, error="user_token_failed", message="Not connected to Simkl")
            return {}
        return {"Authorization": f"Bearer {self._access_token}"}

    def _raise_if_error(self, response: Any) -> None:
        if response.status == 204 or (200 <= response.status < 300):
            return
        body = response.json({}) or {}
        raise SimklApiError(
            response.status,
            error=str(body.get("error") or ""),
            message=str(body.get("message") or ""),
            body=body,
        )

    # -- verbs ------------------------------------------------------------

    def get(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        require_auth: bool = True,
        allowed_statuses: tuple = (),
    ) -> Any:
        response = self._http.get(
            BASE_URL + path,
            params=self._params(params),
            headers=self._headers(require_auth=require_auth),
            authenticated=False,  # host injects nothing for auth.kind="custom"
            allowed_statuses=allowed_statuses,
        )
        if response.status in allowed_statuses:
            return response.json({})
        self._raise_if_error(response)
        return response.json({})

    def post(
        self,
        path: str,
        *,
        json: Any = None,
        params: Optional[Dict[str, Any]] = None,
        require_auth: bool = True,
    ) -> Any:
        response = self._http.post(
            BASE_URL + path,
            json=json,
            params=self._params(params),
            headers=self._headers(require_auth=require_auth),
            authenticated=False,
        )
        self._raise_if_error(response)
        return response.json({})

    def delete(self, path: str, *, params: Optional[Dict[str, Any]] = None) -> None:
        response = self._http.delete(
            BASE_URL + path,
            params=self._params(params),
            headers=self._headers(require_auth=True),
            authenticated=False,
        )
        self._raise_if_error(response)


__all__ = ["APP_NAME", "APP_VERSION", "BASE_URL", "SimklClient"]
