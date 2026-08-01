"""Builds the Continue Watching list from Simkl's playback + watching-list data.

Movies are simple: ``GET /sync/playback/movies`` is already exactly "things
in progress". Simkl auto-completes anything scrobbled to stop() at >=80%, so a
finished movie has already dropped out of that list by the time we ask.

Shows are driven by two different, deliberately separate signals:

  * **List membership** — whether a show belongs in Continue Watching at all —
    comes *only* from ``GET /sync/all-items/shows/watching``: any show that
    isn't fully watched belongs here, regardless of whether anything is
    currently paused mid-episode. A show sitting at 40/62 watched with nothing
    open right now is exactly as much "still watching" as one with a paused
    episode.

  * **The exact resume point** (which episode, and — if it's genuinely paused
    mid-playback — how far into it) is a secondary enrichment from
    ``GET /sync/playback/episodes``, applied *if* it happens to have a
    matching open session. Its absence — which is the common case; Simkl
    returns ``null`` there whenever nothing is currently paused — must never
    remove a show from the list. The main Movies/Shows widget doesn't even
    read ``resume_season``/``resume_episode`` (see ``widget_section.dart``);
    only the Detail Page needs the precise resume point, and it resolves that
    itself via ``tracker.item_progress``, independently, when the item is
    opened. So best-effort here is genuinely all that's needed.

Get this backwards — as an earlier version of this file did — and gating list
membership on resolving a resume episode means one upstream field failing to
parse (or, as here, simply not being present because nothing is paused) empties
the entire Shows row.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import playback
from .util import coerce_int, epoch, tmdb_of


def _next_episode(raw: Any) -> tuple[Optional[int], Optional[int]]:
    """Simkl's docs describe ``next_to_watch`` loosely; read defensively."""

    if not isinstance(raw, dict):
        return None, None
    season = raw.get("season")
    number = raw.get("episode")
    if number is None:
        number = raw.get("number")
    return coerce_int(season), coerce_int(number)


def movies(client: Any, *, limit: int) -> List[Dict[str, Any]]:
    sessions = playback.movie_sessions(client)
    items = [
        {
            "media": {
                "type": "movie",
                "ids": {"tmdb": s["tmdb"]},
                "title": s["title"],
                "year": s["year"],
            },
            "progress": s["progress"],
            "resume_available": True,
            "playback_id": s["playback_id"],
            "is_scrobbled": True,
            "sort_key": s["sort_key"],
        }
        for s in sessions
    ]
    items.sort(key=lambda item: item["sort_key"], reverse=True)
    return items[:limit]


def shows(client: Any, *, limit: int) -> List[Dict[str, Any]]:
    scrobble_map = {s["tmdb"]: s for s in playback.episode_sessions(client)}

    watching = client.get(
        "/sync/all-items/shows/watching", params={"next_watch_info": "yes"}
    ) or {}
    rows = watching.get("shows") or []

    items: List[Dict[str, Any]] = []
    seen_tmdb: set[int] = set()

    for row in rows:
        show = row.get("show") or {}
        tmdb = tmdb_of(show.get("ids"))
        if tmdb is None:
            continue
        watched_count = coerce_int(row.get("watched_episodes_count")) or 0
        total_count = coerce_int(row.get("total_episodes_count")) or 0
        if total_count and watched_count >= total_count:
            continue  # fully watched — not a Continue Watching item
        seen_tmdb.add(tmdb)

        overall_progress = (
            watched_count / total_count * 100.0 if total_count else 0.0
        )
        last_activity = epoch(row.get("last_watched_at"))
        session = scrobble_map.get(tmdb)

        if session is not None:
            # Prefer the show's overall progress once we know episode counts —
            # the raw scrobble percentage only describes one episode, not the
            # show as a whole.
            season, episode = session["season"], session["episode"]
            is_scrobbled = True
            progress = overall_progress if total_count else session["progress"]
            resume_playback_id = session["playback_id"]
            sort_key = max(session["sort_key"], last_activity)
        else:
            # Best-effort only: nothing downstream requires this to resolve.
            season, episode = _next_episode(row.get("next_to_watch"))
            is_scrobbled = False
            progress = overall_progress
            resume_playback_id = None
            sort_key = last_activity

        item: Dict[str, Any] = {
            "media": {
                "type": "show",
                "ids": {"tmdb": tmdb},
                "title": show.get("title"),
                "year": show.get("year"),
            },
            "progress": round(progress, 1),
            "resume_available": True,
            "is_scrobbled": is_scrobbled,
            "sort_key": sort_key,
        }
        if season is not None and episode is not None:
            item["resume_season"] = season
            item["resume_episode"] = episode
        if resume_playback_id is not None:
            item["resume_playback_id"] = resume_playback_id
        items.append(item)

    # A show can have an open episode session before the watching-list has
    # caught up with it (right after pressing Play on episode 1 of something
    # new) — still worth surfacing even with no counts yet.
    for tmdb, session in scrobble_map.items():
        if tmdb in seen_tmdb:
            continue
        items.append(
            {
                "media": {
                    "type": "show",
                    "ids": {"tmdb": tmdb},
                    "title": session["title"],
                    "year": session["year"],
                },
                "progress": session["progress"],
                "resume_available": True,
                "resume_season": session["season"],
                "resume_episode": session["episode"],
                "resume_playback_id": session["playback_id"],
                "is_scrobbled": True,
                "sort_key": session["sort_key"],
            }
        )

    items.sort(key=lambda item: item["sort_key"], reverse=True)
    return items[:limit]


def build(client: Any, *, media_type: str, limit: int) -> List[Dict[str, Any]]:
    return movies(client, limit=limit) if media_type == "movie" else shows(client, limit=limit)


__all__ = ["build", "movies", "shows"]
