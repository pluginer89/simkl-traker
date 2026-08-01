"""Small helpers shared across the plugin's modules."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional


def coerce_int(value: Any) -> Optional[int]:
    """Simkl's ``ids.tmdb`` is sometimes a string, sometimes an int, sometimes null."""

    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def tmdb_of(ids: Any) -> Optional[int]:
    if not isinstance(ids, Mapping):
        return None
    return coerce_int(ids.get("tmdb"))


def epoch(iso_ts: Any) -> float:
    """Parse a Simkl ISO-8601 timestamp into epoch seconds; 0.0 if unparseable."""

    if not iso_ts:
        return 0.0
    try:
        text = str(iso_ts)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return 0.0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def media_tmdb_id(media: Mapping[str, Any]) -> Optional[int]:
    """The tmdb id a MediaRef-shaped dict is keyed by.

    For an episode this is the *show's* tmdb id — Simkl (like the host) tracks
    shows, not individual episodes, so every lookup here is show-scoped.
    """

    if media.get("type") == "episode":
        show = media.get("show") or {}
        return tmdb_of(show.get("ids"))
    return tmdb_of(media.get("ids"))


__all__ = [
    "coerce_float",
    "coerce_int",
    "epoch",
    "media_tmdb_id",
    "now_iso",
    "tmdb_of",
]
