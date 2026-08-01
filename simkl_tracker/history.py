"""Mark-watched and resume-point removal.

Simkl models watch history and "in progress" as separate concepts — adding a
history entry does not, by itself, clear an open playback session, so a
mark-watched action here does not implicitly remove the item from Continue
Watching. The host handles that by calling
``remove_from_continue_watching`` right after ``mark_watched`` whenever a
``playback_id`` is available, same as it already does for the built-in Trakt
path (see ``routes/library.py``).
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from .errors import SimklApiError
from .util import media_tmdb_id, now_iso


def mark_watched(
    client: Any, media: Mapping[str, Any], *, watched_at: Optional[str] = None
) -> Dict[str, Any]:
    stamp = watched_at or now_iso()
    tmdb_id = media_tmdb_id(media)
    if tmdb_id is None:
        raise ValueError("mark_watched requires a resolvable tmdb id")

    media_type = media.get("type")

    if media_type == "movie":
        body = {"movies": [{"ids": {"tmdb": tmdb_id}, "watched_at": stamp}]}
        client.post("/sync/history", json=body)
        return {"synced": True, "tmdb_id": tmdb_id}

    if media_type == "episode":
        season = media.get("season")
        episode = media.get("episode")
        if season is None or episode is None:
            raise ValueError("mark_watched for an episode requires season and episode")
        body = {
            "shows": [
                {
                    "ids": {"tmdb": tmdb_id},
                    "seasons": [
                        {"number": season, "episodes": [{"number": episode, "watched_at": stamp}]}
                    ],
                }
            ]
        }
        client.post("/sync/history", json=body)
        return {"synced": True, "tmdb_id": tmdb_id}

    # Whole-show mark-watched has no per-episode granularity — Simkl's
    # equivalent is moving the show to the "completed" list, not a history
    # write, so it goes through /sync/add-to-list instead.
    body = {"shows": [{"to": "completed", "ids": {"tmdb": tmdb_id}}]}
    client.post("/sync/add-to-list", json=body)
    return {"synced": True, "tmdb_id": tmdb_id}


def remove_from_continue_watching(client: Any, *, playback_id: Optional[Any]) -> Dict[str, Any]:
    if playback_id is None:
        raise ValueError("remove_from_continue_watching requires a playback_id")
    try:
        client.delete(f"/sync/playback/{int(playback_id)}")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid playback_id '{playback_id}'") from exc
    except SimklApiError as exc:
        if exc.status == 404:
            # Already cleared (e.g. auto-completed by a stop() at >=80%) — the
            # caller's goal ("this should not be in Continue Watching anymore")
            # is already satisfied.
            return {"removed": False, "already_gone": True}
        raise
    return {"removed": True}


__all__ = ["mark_watched", "remove_from_continue_watching"]
