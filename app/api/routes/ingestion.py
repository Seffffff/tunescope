"""
Ingestion routes.
"""

import asyncio
import json

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import IngestionRequest, IngestionResponse
from app.core.logging import get_logger
from app.db.models import AudioFeatures, Playlist, PlaylistTrack, Track
from app.db.repositories.repo import get_playlist_by_spotify_id, get_user_by_spotify_id, upsert_user
from app.db.session import get_db
from app.ingestion.ingestion_service import _upsert_audio_feature, ingest_playlist
from app.ingestion.spotify_client import SpotifyClient

router = APIRouter(prefix="/ingest", tags=["ingestion"])
logger = get_logger(__name__)

KEY_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _get_access_token(authorization: str = Header(...)) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    return authorization[len("Bearer ") :]


@router.post("/playlist/{playlist_id}", response_model=IngestionResponse)
async def ingest_playlist_endpoint(
    playlist_id: str,
    body: IngestionRequest = IngestionRequest(),
    access_token: str = Depends(_get_access_token),
    db: AsyncSession = Depends(get_db),
):
    """
    Ingest a Spotify playlist:
    - Fetch all track metadata
    - Fetch audio features in batches
    - Normalize and upsert into PostgreSQL
    - Store raw payloads for audit/replay

    Pass `force_refetch=true` to re-ingest even if snapshot_id is unchanged.
    """
    async with SpotifyClient(access_token) as client:
        user_profile = await client.get_current_user()
        spotify_user_id = user_profile["id"]

        user = await get_user_by_spotify_id(db, spotify_user_id)
        if not user:
            user = await upsert_user(
                db,
                spotify_id=spotify_user_id,
                display_name=user_profile.get("display_name"),
                email=user_profile.get("email"),
                access_token=access_token,
            )

        # Idempotency check: skip if snapshot_id matches (unless forced)
        if not body.force_refetch:
            existing = await get_playlist_by_spotify_id(db, playlist_id)
            if existing:
                try:
                    raw_pl = await client._get(
                        f"/playlists/{playlist_id}", params={"fields": "snapshot_id,name"}
                    )
                    if raw_pl.get("snapshot_id") == existing.snapshot_id:
                        logger.info(
                            "ingestion_skipped_snapshot_match",
                            playlist_id=playlist_id,
                            snapshot_id=existing.snapshot_id,
                        )
                        return IngestionResponse(
                            playlist_id=playlist_id,
                            tracks_upserted=0,
                            audio_features_upserted=0,
                            error_count=0,
                            errors=[],
                            skipped=True,
                            skip_reason="snapshot_id unchanged — playlist hasn't changed since last ingest. Use Force Re-run to override.",
                        )
                except Exception:
                    pass  # If snapshot check fails, proceed with ingestion

        try:
            stats = await ingest_playlist(
                session=db,
                client=client,
                playlist_spotify_id=playlist_id,
                owner_id=user.id,
            )
        except Exception as exc:
            logger.error("ingestion_pipeline_failed", playlist_id=playlist_id, error=str(exc))
            raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}") from exc

    return IngestionResponse(
        playlist_id=playlist_id,
        tracks_upserted=stats["tracks_upserted"],
        audio_features_upserted=stats["audio_features_upserted"],
        audio_features_needed=stats.get("audio_features_needed", 0),
        audio_features_from_cache=stats.get("audio_features_from_cache", 0),
        audio_features_rb_returned=stats.get("audio_features_rb_returned", 0),
        audio_features_not_found=stats.get("audio_features_not_found", 0),
        elapsed_seconds=stats.get("elapsed_seconds", 0.0),
        error_count=len(stats.get("errors", [])),
        errors=stats.get("errors", []),
    )


@router.get("/playlist/{playlist_id}/tracks")
async def get_playlist_tracks(
    playlist_id: str,
    access_token: str = Depends(_get_access_token),
    db: AsyncSession = Depends(get_db),
):
    """
    Return analyzed and missing tracks for a playlist.
    Used by the frontend track panel after ingestion.
    """

    result = await db.execute(
        select(Track, AudioFeatures, PlaylistTrack.position)
        .join(PlaylistTrack, PlaylistTrack.track_id == Track.id)
        .join(Playlist, Playlist.id == PlaylistTrack.playlist_id)
        .outerjoin(AudioFeatures, AudioFeatures.track_id == Track.id)
        .where(Playlist.spotify_id == playlist_id)
        .order_by(PlaylistTrack.position)
    )
    rows = result.all()

    if not rows:
        raise HTTPException(status_code=404, detail="Playlist not found or not yet ingested")

    analyzed = []
    missing = []

    for track, af, position in rows:
        if af is not None and af.tempo is not None:
            key_name = KEY_NAMES[af.key] if af.key is not None else None
            analyzed.append(
                {
                    "spotify_id": track.spotify_id,
                    "name": track.name,
                    "artist": track.artist,
                    "album": track.album,
                    "tempo": af.tempo,
                    "key": af.key,
                    "key_name": af.key_name or key_name,
                    "mode": af.mode,
                    "mode_name": af.mode_name,
                    "energy": af.energy,
                    "danceability": af.danceability,
                    "valence": af.valence,
                    "loudness": af.loudness,
                    "duration_ms": track.duration_ms,
                    "ultimate_guitar_url": track.ultimate_guitar_url,
                    "spotify_url": f"https://open.spotify.com/track/{track.spotify_id}",
                    "position": position,
                    # "analysis_source": (af.raw_json or {}).get("analysis_source", "reccobeats"),
                }
            )
        else:
            missing.append(
                {
                    "spotify_id": track.spotify_id,
                    "name": track.name,
                    "artist": track.artist,
                    "album": track.album,
                    "duration_ms": track.duration_ms,
                    "tempo": None,
                    "key": None,
                    "position": position,
                    # "analysis_source": None,
                    "ultimate_guitar_url": track.ultimate_guitar_url,
                    "spotify_url": f"https://open.spotify.com/track/{track.spotify_id}",
                }
            )

    return {
        "playlist_id": playlist_id,
        "analyzed_count": len(analyzed),
        "missing_count": len(missing),
        "analyzed": analyzed,
        "missing": missing,
    }


@router.post("/playlist/{playlist_id}/analyze-missing")
async def analyze_missing_tracks(
    playlist_id: str,
    access_token: str = Depends(_get_access_token),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger YouTube-based analysis for tracks still missing audio features.
    Returns per-track results so the frontend can update the table live.
    """
    from app.ingestion.manual_analyzer import batch_analyze_from_youtube

    result = await db.execute(
        select(Track)
        .join(PlaylistTrack, PlaylistTrack.track_id == Track.id)
        .join(Playlist, Playlist.id == PlaylistTrack.playlist_id)
        .outerjoin(AudioFeatures, AudioFeatures.track_id == Track.id)
        .where(
            Playlist.spotify_id == playlist_id,
            or_(AudioFeatures.id.is_(None), AudioFeatures.tempo.is_(None)),
        )
    )
    missing_tracks = result.scalars().all()

    if not missing_tracks:
        return {"succeeded": 0, "failed": 0, "attempted": 0, "results": []}

    tracks_payload = [
        {"spotify_id": t.spotify_id, "name": t.name, "artist": t.artist} for t in missing_tracks
    ]

    analysis_results = await batch_analyze_from_youtube(tracks_payload)

    stats = {"audio_features_upserted": 0, "errors": []}
    per_track_results = []

    for track in missing_tracks:
        sid = track.spotify_id
        feat = analysis_results.get(sid)

        if feat:
            await _upsert_audio_feature(db, sid, feat, stats)
            per_track_results.append(
                {
                    "spotify_id": sid,
                    "status": "success",
                    "tempo": feat.get("tempo"),
                    "key": feat.get("key"),
                    "mode": feat.get("mode"),
                    # "analysis_source": feat.get("analysis_source"),
                }
            )
        else:
            per_track_results.append(
                {
                    "spotify_id": sid,
                    "status": "failed",
                }
            )

    succeeded = stats["audio_features_upserted"]
    failed = len(missing_tracks) - succeeded

    return {
        "succeeded": succeeded,
        "failed": failed,
        "attempted": len(missing_tracks),
        "results": per_track_results,
        "errors": stats["errors"],
    }


@router.get("/playlist/{playlist_id}/analyze-stream")
async def analyze_stream(
    playlist_id: str,
    authorization: str | None = None,  # query param fallback for EventSource
    db: AsyncSession = Depends(get_db),
):
    # EventSource can't set headers, so accept token as ?authorization=Bearer+xyz
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    access_token = authorization[len("Bearer ") :]  # noqa: F841

    """
    SSE endpoint. Streams Recco results as they arrive, and immediately starts
    YouTube fallback for each batch's misses in parallel — so the user sees
    analyzed tracks appear ASAP while YT jobs run concurrently.

    Event types emitted:
      {type: "start",    total: N}
      {type: "track",    spotify_id, name, artist, tempo, key, key_name, mode,
                         mode_name, energy, danceability, valence, loudness,
                         source: "recco"|"youtube", done: N, total: N}
      {type: "miss",     spotify_id, name, artist}   -- queued for YT immediately
      {type: "yt_failed", spotify_id}                -- YT also came up empty
      {type: "done",     analyzed: N, missing: N}
      {type: "error",    message}
    """
    from app.ingestion.audio_analyzer import batch_analyze_previews
    from app.ingestion.manual_analyzer import _analyze_one

    async def event_stream():
        def sse(obj: dict) -> str:
            return "data: " + json.dumps(obj) + "\n\n"

        def build_track_event(sid: str, feat: dict, track_obj, done: int, source: str) -> dict:
            key_val = feat.get("key")
            mode_val = feat.get("mode")
            return {
                "type": "track",
                "spotify_id": sid,
                "name": track_obj.name if track_obj else sid,
                "artist": track_obj.artist if track_obj else "",
                "album": track_obj.album if track_obj else None,
                "duration_ms": track_obj.duration_ms if track_obj else None,
                "ultimate_guitar_url": track_obj.ultimate_guitar_url if track_obj else None,
                "spotify_url": f"https://open.spotify.com/track/{sid}",
                "tempo": feat.get("tempo"),
                "key": key_val,
                "key_name": KEY_NAMES[key_val] if key_val is not None else None,
                "mode": mode_val,
                "mode_name": "maj" if mode_val == 1 else "min" if mode_val == 0 else None,
                "energy": feat.get("energy"),
                "danceability": feat.get("danceability"),
                "valence": feat.get("valence"),
                "loudness": feat.get("loudness"),
                "source": source,
                "done": done,
                "total": total,
            }

        # ── load missing tracks ──────────────────────────────────────────────
        try:
            result = await db.execute(
                select(Track)
                .join(PlaylistTrack, PlaylistTrack.track_id == Track.id)
                .join(Playlist, Playlist.id == PlaylistTrack.playlist_id)
                .outerjoin(AudioFeatures, AudioFeatures.track_id == Track.id)
                .where(
                    Playlist.spotify_id == playlist_id,
                    or_(AudioFeatures.id.is_(None), AudioFeatures.tempo.is_(None)),
                )
                .order_by(PlaylistTrack.position)
            )
            missing_tracks = result.scalars().all()
        except Exception as exc:
            yield sse({"type": "error", "message": str(exc)})
            return

        if not missing_tracks:
            yield sse({"type": "done", "analyzed": 0, "missing": 0})
            return

        total = len(missing_tracks)
        yield sse({"type": "start", "total": total})

        track_map = {t.spotify_id: t for t in missing_tracks}
        tracks_payload = [
            {"spotify_id": t.spotify_id, "name": t.name, "artist": t.artist} for t in missing_tracks
        ]

        stats = {"audio_features_upserted": 0, "errors": []}
        done_count = 0

        _queue: asyncio.Queue = asyncio.Queue()

        # Semaphore caps concurrent YT downloads across all batches
        yt_sem = asyncio.Semaphore(4)

        # ── YT worker — spawned the moment Recco misses a track ─────────────
        async def analyze_one_yt(track_info: dict):
            sid = track_info["spotify_id"]
            async with yt_sem:
                feat = await _analyze_one(track_info)
            if feat:
                await _queue.put(("yt_found", sid, feat))
            else:
                await _queue.put(("yt_miss", sid))

        # ── Recco batch callback: fires after every 5-song batch ─────────────
        async def on_batch(found: dict, missed: list[dict]):
            for sid, feat in found.items():
                await _queue.put(("rb_found", sid, feat))
            for track_info in missed:
                await _queue.put(("miss", track_info["spotify_id"]))
                asyncio.create_task(analyze_one_yt(track_info))

        # ── Recco task ───────────────────────────────────────────────────────
        async def run_reccobeats():
            await batch_analyze_previews(tracks_payload, on_batch_complete=on_batch)
            await _queue.put(("rb_done",))

        rb_task = asyncio.create_task(run_reccobeats())

        # ── Single queue consumer drives all SSE output ──────────────────────
        yt_pending = 0
        rb_finished = False

        while True:
            item = await _queue.get()
            tag = item[0]

            if tag == "rb_found":
                _, sid, feat = item
                track = track_map.get(sid)
                done_count += 1
                await _upsert_audio_feature(db, sid, feat, stats)
                try:
                    await db.commit()
                except Exception:
                    await db.rollback()
                yield sse(build_track_event(sid, feat, track, done_count, "recco"))

            elif tag == "miss":
                _, sid = item
                yt_pending += 1
                track = track_map.get(sid)
                yield sse(
                    {
                        "type": "miss",
                        "spotify_id": sid,
                        "name": track.name if track else sid,
                        "artist": track.artist if track else "",
                    }
                )

            elif tag == "yt_found":
                _, sid, feat = item
                track = track_map.get(sid)
                yt_pending -= 1
                done_count += 1
                await _upsert_audio_feature(db, sid, feat, stats)
                try:
                    await db.commit()
                except Exception:
                    await db.rollback()
                yield sse(build_track_event(sid, feat, track, done_count, "youtube"))

            elif tag == "yt_miss":
                _, sid = item
                yt_pending -= 1
                yield sse({"type": "yt_failed", "spotify_id": sid})

            elif tag == "rb_done":
                rb_finished = True

            # Stream closes once Recco is done AND every YT job has resolved
            if rb_finished and yt_pending == 0:
                break

        await rb_task  # surface any uncaught Recco exception

        analyzed_total = stats["audio_features_upserted"]
        missing_total = total - analyzed_total
        yield sse({"type": "done", "analyzed": analyzed_total, "missing": missing_total})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/manual-analyze")
async def manual_analyze_track(
    request: dict,
    authorization: str = Header(...),
    db: AsyncSession = Depends(get_db),
):
    from app.ingestion.manual_analyzer import _analyze_one

    spotify_id = request.get("spotify_id")
    if not spotify_id:
        raise HTTPException(status_code=400, detail="spotify_id required")

    track_info = {
        "spotify_id": spotify_id,
        "name": request.get("name", ""),
        "artist": request.get("artist", ""),
    }

    feat = await _analyze_one(track_info)
    if not feat:
        return {"status": "failed", "spotify_id": spotify_id}

    stats = {"audio_features_upserted": 0, "errors": []}
    await _upsert_audio_feature(db, spotify_id, feat, stats)
    await db.commit()

    key_val = feat.get("key")
    mode_val = feat.get("mode")
    return {
        "status": "success",
        "spotify_id": spotify_id,
        "tempo": feat.get("tempo"),
        "key": key_val,
        "key_name": KEY_NAMES[key_val] if key_val is not None else None,
        "mode": mode_val,
        "mode_name": "maj" if mode_val == 1 else "min" if mode_val == 0 else None,
        "energy": feat.get("energy"),
        "danceability": feat.get("danceability"),
        "valence": feat.get("valence"),
    }
