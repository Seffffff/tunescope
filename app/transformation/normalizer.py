"""
Transformation layer.

Converts raw audio analysis results (from librosa) or Spotify API responses
into normalized domain objects ready for storage.

Design decision: Pure functions (no DB or IO) so they are trivially testable.
"""
from datetime import datetime
from app.algorithms.key_normalization import normalize_key


def transform_track(raw_item: dict) -> dict:
    """
    Transform a playlist track item into a Track dict.
    raw_item is the wrapper object from /playlists/{id}/tracks.
    """
    track = raw_item.get("track", {}) or {}
    artists = track.get("artists") or []
    primary_artist = artists[0].get("name", "Unknown") if artists else "Unknown"
    album = (track.get("album") or {}).get("name")

    return {
        "spotify_id": track["id"],
        "name": track.get("name", "Unknown"),
        "artist": primary_artist,
        "album": album,
        "duration_ms": track.get("duration_ms", 0),
        "explicit": track.get("explicit", False),
        "popularity": track.get("popularity"),
    }


def transform_audio_features(raw: dict) -> dict:
    """
    Transform raw audio features (from librosa or Spotify) into the normalized schema.
    Includes derived fields (key_name, mode_name) computed via our algorithm.
    Extra fields like analysis_source are passed through in raw_json.
    """
    key = raw.get("key")
    mode = raw.get("mode")
    key_name, mode_name = normalize_key(key, mode)

    # Build raw_json — strip out internal-only fields that aren't part of the schema
    raw_json = {k: v for k, v in raw.items() if k not in ("key_name", "mode_name")}

    return {
        "tempo": raw.get("tempo"),
        "key": key,
        "mode": mode,
        "time_signature": raw.get("time_signature"),
        "energy": raw.get("energy"),
        "danceability": raw.get("danceability"),
        "valence": raw.get("valence"),
        "acousticness": raw.get("acousticness"),
        "instrumentalness": raw.get("instrumentalness"),
        "liveness": raw.get("liveness"),
        "loudness": raw.get("loudness"),
        "speechiness": raw.get("speechiness"),
        "key_name": key_name,
        "mode_name": mode_name,
        "raw_json": raw_json,
    }


def transform_playlist(raw: dict, owner_id: str) -> dict:
    """Transform a raw Spotify playlist object."""
    return {
        "spotify_id": raw["id"],
        "owner_id": owner_id,
        "name": raw.get("name", "Unnamed Playlist"),
        "description": raw.get("description"),
        "snapshot_id": raw.get("snapshot_id"),
        "track_count": (raw.get("tracks") or {}).get("total", 0),
    }


def parse_added_at(added_at_str: str | None) -> datetime | None:
    if not added_at_str:
        return None
    try:
        return datetime.fromisoformat(added_at_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None