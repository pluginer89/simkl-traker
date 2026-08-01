"""Per-item progress — the movie-percentage or show-episode-grid shape the host
route expects (matches ``discovery.py``'s original Trakt response 1:1)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from . import playback
from .errors import SimklApiError
from .util import coerce_int


def _resolve_simkl_id(client: Any, tmdb_id: int, media_type: str) -> Optional[int]:
    """Translate a tmdb id to Simkl's own internal id via ``GET /search/id``.

    ``POST /sync/watched`` matching by ``{"ids": {"tmdb": ...}}`` is not
    reliable — verified against a real account: for a show that is genuinely
    in the watching list, it either resolves to a *different* show entirely
    (a stale/incorrect crosswalk entry) or returns ``not_found`` outright,
    while the identical query using Simkl's own id for that same show returns
    full data immediately. ``/sync/all-items/shows/watching`` and
    ``/sync/playback/*`` don't have this problem — they hand back whatever ids
    Simkl already has on file for items already in your library, no id
    *lookup* involved — so this resolution step is specific to endpoints like
    ``/sync/watched`` that require the caller to supply a matching id.

    Movies haven't been observed to hit this, but resolving through Simkl's
    own id either way is the same one extra call and removes any doubt.
    """

    try:
        results = client.get(
            "/search/id", params={"tmdb": tmdb_id, "type": media_type}
        )
    except SimklApiError:
        return None
    if not isinstance(results, list) or not results:
        return None
    return coerce_int((results[0].get("ids") or {}).get("simkl"))


def _watched_ids(client: Any, tmdb_id: int, media_type: str) -> Dict[str, Any]:
    simkl_id = _resolve_simkl_id(client, tmdb_id, media_type)
    return {"simkl": simkl_id} if simkl_id is not None else {"tmdb": tmdb_id}


def movie(client: Any, tmdb_id: int) -> Dict[str, Any]:
    for session in playback.movie_sessions(client):
        if session["tmdb"] == tmdb_id:
            return {
                "type": "movie",
                "progress": session["progress"],
                "resume_available": True,
                "playback_id": session["playback_id"],
                "watched": False,
            }

    # Not paused — check whether it was already watched to completion.
    try:
        ids = _watched_ids(client, tmdb_id, "movie")
        rows = client.post("/sync/watched", json=[{"ids": ids}]) or []
    except SimklApiError:
        rows = []
    entry = rows[0] if rows else None
    watched = bool(entry and entry.get("result") not in (False, None, "not_found"))
    return {
        "type": "movie",
        "progress": 100.0 if watched else 0.0,
        "resume_available": False,
        "watched": watched,
    }


def show(client: Any, tmdb_id: int) -> Dict[str, Any]:
    ids = _watched_ids(client, tmdb_id, "tv")
    rows = client.post(
        "/sync/watched",
        json=[{"ids": ids}],
        params={"extended": "episodes"},
    ) or []
    entry = rows[0] if rows else None
    if not entry or entry.get("result") in (False, None, "not_found"):
        raise SimklApiError(404, error="not_found", message="No watched progress found")

    scrobble_by_ep = playback.episode_sessions_by_show(client, tmdb_id)

    seasons_out = []
    for season in entry.get("seasons") or []:
        season_number = coerce_int(season.get("number"))
        episodes_out = []
        for ep in season.get("episodes") or []:
            ep_number = coerce_int(ep.get("number"))
            session = scrobble_by_ep.get((season_number, ep_number))
            episodes_out.append(
                {
                    "number": ep_number,
                    "completed": bool(ep.get("watched")),
                    "last_watched_at": ep.get("last_watched_at"),
                    "scrobble_progress": session["progress"] if session else None,
                    "playback_id": session["playback_id"] if session else None,
                }
            )
        seasons_out.append(
            {
                "number": season_number,
                "aired": season.get("episodes_aired"),
                "completed": season.get("episodes_watched"),
                "episodes": episodes_out,
            }
        )

    return {
        "type": "show",
        "trakt_id": None,
        "aired": entry.get("episodes_aired"),
        "completed": entry.get("episodes_watched"),
        "seasons": seasons_out,
    }


__all__ = ["movie", "show"]
