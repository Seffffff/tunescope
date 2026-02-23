"""
Ingestion Service
=================
Orchestrates the full playlist ingestion pipeline:

  1. Fetch raw tracks from Spotify API
  2. Store raw payloads (audit trail)
  3. Upsert tracks and playlist-track links
  4. Fetch audio features from ReccoBeats for tracks missing them
  5. Normalize and upsert audio features
"""
import time
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis_cache import get_cached_audio_features, set_cached_audio_features
from app.core.logging import get_logger
from app.db.models import Track
from app.db.repositories.repo import (
    get_tracks_missing_audio_features,
    store_raw_payload,
    upsert_audio_features,
    upsert_playlist,
    upsert_playlist_track,
    upsert_track,
)
from app.ingestion.audio_analyzer import batch_analyze_previews
from app.ingestion.spotify_client import SpotifyClient
from app.transformation.normalizer import (
    parse_added_at,
    transform_audio_features,
    transform_track,
)
from urllib.parse import quote_plus


logger = get_logger(__name__)



# Regexes applied to both name and artist
_UG_BOTH = [
    r'\s*\((?:feat|ft|with)[^)]*\)',        # (feat. X), (ft. X), (with X)
    r'\s*\bfeat\.?\s+[^,\-\)]+',           # feat. X without parens
]

# Regexes applied to track name only
_UG_NAME = [
    r'\s*-\s*Remix.*',                      # - Remix, - Club Remix, etc.
    r'\s*\((?:remix|edit|version|mix|radio edit|extended)[^)]*\)',
    r'\s*-\s*(?:Original Mix|Radio Edit|Extended Mix).*',
    r'\s*-\s*(?:\d{4}\s+)?(?:Remaster(?:ed)?|Reissue|Deluxe.*|Anniversary.*|Edition.*)',  # - 2025 Remaster, - Remastered, - Deluxe Edition
    r'\s*\((?:\d{4}\s+)?(?:Remaster(?:ed)?|Reissue|Deluxe|Anniversary)[^)]*\)',      # (2025 Remaster), (Remastered), (Deluxe Edition)
]

# Regexes applied to artist only
_UG_ARTIST = [
    # add as needed
]


def _clean_for_ug(s: str, kind: str = "both") -> str:
    patterns = _UG_BOTH[:]
    if kind == "name":
        patterns += _UG_NAME
    elif kind == "artist":
        patterns += _UG_ARTIST
    for pattern in patterns:
        s = re.sub(pattern, '', s, flags=re.IGNORECASE)
    return s.strip()


async def ingest_playlist(
    session: AsyncSession,
    client: SpotifyClient,
    playlist_spotify_id: str,
    owner_id: str,
) -> dict:
    start_time = time.monotonic()
    stats = {
        "playlist_id": playlist_spotify_id,
        "tracks_upserted": 0,
        "audio_features_upserted": 0,
        "audio_features_skipped_no_preview": 0,
        "errors": [],
    }

    logger.info("ingestion_started", playlist_id=playlist_spotify_id)

    # ------------------------------------------------------------------
    # Step 1: Fetch all tracks from Spotify
    # ------------------------------------------------------------------
    raw_tracks_response = await client.get_all_playlist_tracks(playlist_spotify_id)

    await store_raw_payload(
        session,
        entity_type="playlist_tracks",
        spotify_id=playlist_spotify_id,
        payload={"items": raw_tracks_response},
    )

    playlist = await upsert_playlist(
        session,
        spotify_id=playlist_spotify_id,
        owner_id=owner_id,
        name="",
        track_count=len(raw_tracks_response),
    )

    # ------------------------------------------------------------------
    # Step 2: Upsert all tracks
    # ------------------------------------------------------------------
    track_spotify_ids: list[str] = []

    for position, raw_item in enumerate(raw_tracks_response):
        if not raw_item.get("track"):
            continue
        try:
            track_data = transform_track(raw_item)
            clean_name   = _clean_for_ug(track_data['name'], kind="name")
            clean_artist = _clean_for_ug(track_data['artist'], kind="artist")
            track_data["ultimate_guitar_url"] = (
                f"https://www.ultimate-guitar.com/search.php?search_type=title"
                f"&value={quote_plus(f'{clean_artist} {clean_name}')}"
            )
            track = await upsert_track(session, **track_data)
            track_spotify_ids.append(track_data["spotify_id"])

            added_at = parse_added_at(raw_item.get("added_at"))
            await upsert_playlist_track(
                session,
                playlist_id=playlist.id,
                track_id=track.id,
                position=position,
                added_at=added_at,
            )
            stats["tracks_upserted"] += 1

        except Exception as exc:
            logger.error("track_ingestion_error", position=position, error=str(exc))
            stats["errors"].append(f"track[{position}]: {exc}")

    # Commit track upserts independently of the audio feature step
    await session.commit()

    # ------------------------------------------------------------------
    # Step 3: Fetch audio features from ReccoBeats for missing tracks
    # ------------------------------------------------------------------
    missing_ids = await get_tracks_missing_audio_features(session, track_spotify_ids)
    stats["audio_features_needed"] = len(missing_ids)
    logger.info("audio_features_needed", total=len(track_spotify_ids), missing=len(missing_ids))

    if missing_ids:
        # Check cache first
        ids_to_fetch = []
        cache_hits = 0
        for spotify_id in missing_ids:
            cached = await get_cached_audio_features(spotify_id)
            if cached:
                await _upsert_audio_feature(session, spotify_id, cached, stats)
                try:
                    await session.commit()
                except Exception as exc:
                    logger.error("cache_hit_commit_error", spotify_id=spotify_id, error=str(exc))
                    await session.rollback()
                cache_hits += 1
            else:
                ids_to_fetch.append(spotify_id)

        stats["audio_features_from_cache"] = cache_hits

        # Fetch from ReccoBeats only — YT fallback is handled by the SSE stream
        if ids_to_fetch:
            tracks_to_analyze = [{"spotify_id": sid} for sid in ids_to_fetch]
            analysis_results = await batch_analyze_previews(tracks_to_analyze)

            stats["audio_features_rb_returned"] = len(analysis_results)
            stats["audio_features_not_found"] = len(ids_to_fetch) - len(analysis_results)

            for spotify_id, raw_feat in analysis_results.items():
                await _upsert_audio_feature(session, spotify_id, raw_feat, stats)
                # Commit per feature so a later failure doesn't roll back earlier writes
                try:
                    await session.commit()
                except Exception as exc:
                    logger.error("audio_feature_commit_error", spotify_id=spotify_id, error=str(exc))
                    await session.rollback()
                # Cache failures are non-blocking
                try:
                    await set_cached_audio_features(spotify_id, raw_feat)
                except Exception:
                    pass
        else:
            stats["audio_features_not_found"] = 0

    stats["elapsed_seconds"] = round(time.monotonic() - start_time, 3)

    logger.info(
        "ingestion_completed",
        **{k: v for k, v in stats.items() if k != "errors"},
        error_count=len(stats["errors"]),
    )
    return stats


async def _upsert_audio_feature(
    session: AsyncSession,
    spotify_id: str,
    raw_feat: dict,
    stats: dict,
) -> None:
    try:
        if session.in_transaction():
            await session.rollback()
            
        result = await session.execute(
            select(Track).where(Track.spotify_id == spotify_id)
        )
        track = result.scalar_one_or_none()
        if not track:
            logger.warning("audio_feature_track_not_found", spotify_id=spotify_id)
            return

        transformed = transform_audio_features(raw_feat)
        await upsert_audio_features(session, track_id=track.id, **transformed)
        stats["audio_features_upserted"] += 1

    except Exception as exc:
        logger.error("audio_feature_upsert_error", spotify_id=spotify_id, error=str(exc))
        stats["errors"].append(f"audio_features[{spotify_id}]: {exc}")