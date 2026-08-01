"""Reads Simkl's paused-playback sessions (``GET /sync/playback/...``).

This is Simkl's equivalent of Trakt's ``/sync/playback/{movies,episodes}`` — the
one place resume percentage actually lives. Both Continue Watching and
per-item progress need it, so it is fetched once here and shared rather than
duplicated.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .util import coerce_float, epoch, tmdb_of


def movie_sessions(client: Any) -> List[Dict[str, Any]]:
    """Normalised open movie sessions, newest first is not guaranteed — sort by
    ``sort_key`` (paused_at, epoch seconds) at the call site."""

    raw = client.get("/sync/playback/movies", params={"hide_watched": "true"}) or []
    out: List[Dict[str, Any]] = []
    for entry in raw:
        movie = entry.get("movie") or {}
        tmdb = tmdb_of(movie.get("ids"))
        if tmdb is None:
            continue
        out.append(
            {
                "tmdb": tmdb,
                "title": movie.get("title"),
                "year": movie.get("year"),
                "progress": coerce_float(entry.get("progress")),
                "playback_id": entry.get("id"),
                "sort_key": epoch(entry.get("paused_at")),
            }
        )
    return out


def episode_sessions(client: Any) -> List[Dict[str, Any]]:
    """Normalised open episode sessions, one per show currently mid-episode."""

    raw = client.get("/sync/playback/episodes", params={"hide_watched": "true"}) or []
    out: List[Dict[str, Any]] = []
    for entry in raw:
        show = entry.get("show") or {}
        tmdb = tmdb_of(show.get("ids"))
        if tmdb is None:
            continue
        episode = entry.get("episode") or {}
        out.append(
            {
                "tmdb": tmdb,
                "title": show.get("title"),
                "year": show.get("year"),
                "season": episode.get("season"),
                "episode": episode.get("number"),
                "progress": coerce_float(entry.get("progress")),
                "playback_id": entry.get("id"),
                "sort_key": epoch(entry.get("paused_at")),
            }
        )
    return out


def episode_sessions_by_show(client: Any, tmdb_id: int) -> Dict[tuple, Dict[str, Any]]:
    """Open episode sessions for one show, keyed by ``(season, number)``."""

    return {
        (s["season"], s["episode"]): s
        for s in episode_sessions(client)
        if s["tmdb"] == tmdb_id and s["season"] is not None and s["episode"] is not None
    }


__all__ = ["episode_sessions", "episode_sessions_by_show", "movie_sessions"]
