"""
ReccoBeats Audio Feature Client
=================================
Fetches audio features (tempo, key, mode, energy, danceability, etc.)
from the ReccoBeats API using Spotify track IDs.

Correct batch endpoint: GET /v1/audio-features?ids=id1,id2,id3
Response: { "content": [ { ...features... }, ... ] }
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

RECCOBEATS_BASE = "https://api.reccobeats.com"
BATCH_SIZE = 5  # small batches so we can stream results incrementally


async def _fetch_batch(client: httpx.AsyncClient, spotify_ids: list[str]) -> list[dict]:
    """Fetch audio features for a batch of Spotify IDs."""
    try:
        response = await client.get(
            "/v1/audio-features",
            params={"ids": ",".join(spotify_ids)},
        )

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 5))
            logger.warning("reccobeats_rate_limited", retry_after=retry_after)
            await asyncio.sleep(retry_after)
            return []

        if not response.is_success:
            logger.warning("reccobeats_batch_error", status=response.status_code, body=response.text[:500])
            return []

        data = response.json()

        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            items = data.get("content") or data.get("audioFeatures") or data.get("data") or data.get("items") or []
            if not items and "content" not in data:
                logger.warning("reccobeats_unknown_response_shape", keys=list(data.keys()))
            return items
        else:
            logger.warning("reccobeats_unexpected_response_type", type=type(data).__name__)
            return []

    except asyncio.TimeoutError:
        logger.error("reccobeats_timeout", count=len(spotify_ids))
        return []
    except Exception as exc:
        logger.error("reccobeats_batch_exception", error=str(exc), type=type(exc).__name__)
        return []


def _parse_feature(feat: dict, sid: str) -> dict:
    return {
        "id": sid,
        "tempo": feat.get("tempo"),
        "key": feat.get("key"),
        "mode": feat.get("mode"),
        "time_signature": feat.get("timeSignature") or feat.get("time_signature"),
        "energy": feat.get("energy"),
        "danceability": feat.get("danceability"),
        "valence": feat.get("valence"),
        "acousticness": feat.get("acousticness"),
        "instrumentalness": feat.get("instrumentalness"),
        "liveness": feat.get("liveness"),
        "loudness": feat.get("loudness"),
        "speechiness": feat.get("speechiness"),
        "analysis_source": "reccobeats",
    }


async def batch_analyze_previews(
    tracks: list[dict],
    on_batch_complete: Any | None = None,
    # async callback(found: dict[str,dict], missed: list[dict])
    # `missed` contains the original track dicts {spotify_id, name, artist}
    # for IDs that ReccoBeats returned nothing for in this batch.
) -> dict[str, dict]:
    """
    Fetch audio features from ReccoBeats in small batches (BATCH_SIZE IDs each).

    on_batch_complete(found, missed) is awaited after every batch so callers
    can stream found results immediately AND kick off YT fallback jobs for misses
    without waiting for the full Recco pass to finish.
    """
    if not tracks:
        return {}

    # Keep full track dicts so we can pass name/artist to the miss callback
    track_map = {t["spotify_id"]: t for t in tracks}
    spotify_ids = list(track_map.keys())
    results: dict[str, dict] = {}

    batches = [
        spotify_ids[i: i + BATCH_SIZE]
        for i in range(0, len(spotify_ids), BATCH_SIZE)
    ]

    # Per-batch timeout — small batches should respond quickly
    timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0)
    async with httpx.AsyncClient(
        base_url=RECCOBEATS_BASE,
        timeout=timeout,
        headers={"Accept": "application/json"},
    ) as client:
        for i, batch in enumerate(batches):
            logger.info("reccobeats_fetching_batch", batch=i + 1, total=len(batches), size=len(batch))
            features_list = await _fetch_batch(client, batch)

            batch_set = set(batch)
            batch_results: dict[str, dict] = {}

            for feat in features_list:
                # ReccoBeats returns their own internal UUID in "id", not the Spotify ID.
                # The Spotify ID is embedded in the "href" field:
                #   https://open.spotify.com/track/<spotify_id>
                spotify_sid = None
                href = feat.get("href", "")
                if "/track/" in href:
                    spotify_sid = href.split("/track/")[-1].split("?")[0].strip()
                if not spotify_sid:
                    # fallbacks in case they change the format
                    spotify_sid = feat.get("spotifyId") or feat.get("spotify_id")
                if not spotify_sid:
                    logger.warning("reccobeats_feature_missing_id", feat_keys=list(feat.keys()), href=href)
                    continue
                if spotify_sid not in batch_set:
                    logger.warning("reccobeats_unexpected_id", spotify_sid=spotify_sid, href=href)
                    continue
                batch_results[spotify_sid] = _parse_feature(feat, spotify_sid)

            results.update(batch_results)

            # Build the miss list for this batch so the caller can start YT fallback immediately
            missed_in_batch = [track_map[sid] for sid in batch if sid not in batch_results]

            if on_batch_complete:
                await on_batch_complete(batch_results, missed_in_batch)

            if i < len(batches) - 1:
                await asyncio.sleep(0.1)

    logger.info(
        "reccobeats_batch_complete",
        total_requested=len(spotify_ids),
        total_returned=len(results),
        not_found=len(spotify_ids) - len(results),
    )
    return results