"""
YouTube → Librosa Analysis
==========================
Pipeline for tracks that ReccoBeats couldn't find by Spotify ID:

  1. Search YouTube for "{artist} - {track name} audio"
  2. Download audio as MP3 via yt-dlp (trimmed to AUDIO_DURATION seconds)
  3. Run local librosa analysis for tempo, key, mode, energy, and proxy features

Tracks run MAX_PARALLEL at a time via asyncio.gather + Semaphore.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import librosa
import numpy as np

from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_PARALLEL = 6
YT_TIMEOUT = 90
AUDIO_DURATION = 30  # seconds to grab


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def batch_analyze_from_youtube(
    tracks: list[dict],  # [{spotify_id, name, artist}, ...]
) -> dict[str, dict | None]:
    """
    Analyze multiple tracks in parallel (capped at MAX_PARALLEL concurrent).
    Returns dict mapping spotify_id -> feature dict (or None if fully failed).
    """
    semaphore = asyncio.Semaphore(MAX_PARALLEL)

    async def _bounded(track: dict) -> tuple[str, dict | None]:
        async with semaphore:
            result = await _analyze_one(track)
            return track["spotify_id"], result

    pairs = await asyncio.gather(
        *[_bounded(t) for t in tracks],
        return_exceptions=True,
    )

    out: dict[str, dict | None] = {}
    for item in pairs:
        if isinstance(item, Exception):
            logger.error("batch_youtube_task_error", error=str(item))
        else:
            sid, feat = item
            out[sid] = feat
    return out


def _warmup_librosa():
    """Pre-load librosa internals so the first real analysis isn't slow."""
    dummy = np.zeros(22050, dtype=np.float32)  # 1 second of silence
    librosa.beat.beat_track(y=dummy, sr=22050)
    librosa.feature.chroma_cqt(y=dummy, sr=22050)


# ---------------------------------------------------------------------------
# Per-track pipeline
# ---------------------------------------------------------------------------


async def _analyze_one(track: dict) -> dict | None:
    """
    Full pipeline for a single track: YouTube download → librosa analysis.
    """
    sid = track["spotify_id"]
    name = track["name"]
    artist = track["artist"]
    query = f"{artist} - {name} audio"

    logger.info("manual_pipeline_start", spotify_id=sid, query=query)

    with tempfile.TemporaryDirectory() as tmpdir:
        wav_path = await _download_mp3(query, sid, tmpdir)
        if not wav_path:
            return None

        try:
            local_features = await asyncio.get_event_loop().run_in_executor(
                None, _librosa_analyze, wav_path, sid
            )
        except Exception as exc:
            logger.error("librosa_analyze_error", spotify_id=sid, error=str(exc))
            local_features = None

        if local_features:
            logger.info("manual_pipeline_success", spotify_id=sid, source="librosa_analyzer")
            return local_features

        logger.error("manual_pipeline_failed", spotify_id=sid)
        return None


# ---------------------------------------------------------------------------
# yt-dlp download
# ---------------------------------------------------------------------------


async def _download_mp3(query: str, spotify_id: str, tmpdir: str) -> str | None:
    """
    Search YouTube and download best audio as .mp3, trimmed to AUDIO_DURATION seconds.
    Returns path to .mp3, or None on failure.
    """
    out_template = os.path.join(tmpdir, f"{spotify_id}.%(ext)s")

    cmd = [
        "yt-dlp",
        "--default-search",
        "ytsearch1",
        "--format",
        "bestaudio[abr<=128]/bestaudio/best",
        "--extract-audio",
        "--audio-format",
        "mp3",
        "--ffmpeg-location",
        "/usr/bin",
        "--download-sections",
        f"*0-{AUDIO_DURATION}",
        "--no-playlist",
        "--quiet",
        "--no-warnings",
        "-o",
        out_template,
        query,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=YT_TIMEOUT)

        if proc.returncode != 0:
            logger.warning(
                "yt_dlp_failed",
                spotify_id=spotify_id,
                returncode=proc.returncode,
                stderr=stderr.decode()[:300],
            )
            return None

    except TimeoutError:
        logger.error("yt_dlp_timeout", spotify_id=spotify_id)
        return None
    except FileNotFoundError:
        logger.error("yt_dlp_not_installed", spotify_id=spotify_id, hint="pip install yt-dlp")
        return None

    audio_files = [f for f in os.listdir(tmpdir) if f.endswith(".mp3") or f.endswith(".wav")]
    if not audio_files:
        logger.warning("yt_dlp_no_audio_output", spotify_id=spotify_id)
        return None

    return os.path.join(tmpdir, audio_files[0])


# ---------------------------------------------------------------------------
# Local librosa analysis (thread pool executor — non-blocking)
# ---------------------------------------------------------------------------


def _librosa_analyze(wav_path: str, spotify_id: str) -> dict:
    """Synchronous librosa analysis run in a thread pool to stay non-blocking."""
    y, sr = librosa.load(wav_path, sr=22050, mono=True, duration=AUDIO_DURATION)

    # BPM — precompute onset envelope to avoid redundant work inside beat_track
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    candidates = librosa.feature.tempo(onset_envelope=onset_env, sr=sr, aggregate=None)
    tempo = float(
        np.median(candidates)
    )  # median across frames is more stable than a single estimate
    beat_frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, bpm=tempo)[1]

    # Correct for double/half tempo — librosa commonly halves or doubles the true BPM
    if tempo < 60:
        tempo *= 2
    elif tempo > 200:
        tempo /= 2

    # Key / Mode via Krumhansl-Schmuckler key profiles
    # chroma_stft is faster than chroma_cqt and accurate enough for key detection
    chroma = librosa.feature.chroma_stft(y=y, sr=sr)
    chroma_mean = chroma.mean(axis=1)

    MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

    major_corr = [np.corrcoef(np.roll(MAJOR, i), chroma_mean)[0, 1] for i in range(12)]
    minor_corr = [np.corrcoef(np.roll(MINOR, i), chroma_mean)[0, 1] for i in range(12)]

    best_major = int(np.argmax(major_corr))
    best_minor = int(np.argmax(minor_corr))

    if major_corr[best_major] >= minor_corr[best_minor]:
        key, mode = best_major, 1
    else:
        key, mode = best_minor, 0

    # Energy — use 90th-percentile RMS so quiet intros/outros don't tank the score,
    # then normalize against the track's own peak so mastering volume doesn't matter.
    rms = librosa.feature.rms(y=y)[0]  # shape (frames,)
    rms_p90 = float(np.percentile(rms, 90))
    rms_peak = float(np.max(rms)) if np.max(rms) > 0 else 1.0
    energy_norm = float(np.clip(rms_p90 / rms_peak, 0.0, 1.0))

    # Proxy features
    spec_centroid = librosa.feature.spectral_centroid(y=y, sr=sr)
    zcr = librosa.feature.zero_crossing_rate(y)

    if len(beat_frames) > 1:
        intervals = np.diff(beat_frames)
        danceability = float(np.clip(1.0 - np.std(intervals) / (np.mean(intervals) + 1e-6), 0, 1))
    else:
        danceability = 0.5

    acousticness = float(np.clip(1.0 - float(np.mean(spec_centroid)) / (sr / 2), 0, 1))
    speechiness = float(np.clip(float(np.mean(zcr)) * 10, 0, 1))
    loudness = float(20 * np.log10(float(np.mean(rms)) + 1e-9))

    return {
        "id": spotify_id,
        "tempo": round(tempo, 2),
        "key": key,
        "mode": mode,
        "time_signature": 4,
        "energy": round(energy_norm, 4),
        "danceability": round(danceability, 4),
        "valence": None,
        "acousticness": round(acousticness, 4),
        "instrumentalness": None,
        "liveness": None,
        "loudness": round(loudness, 2),
        "speechiness": round(speechiness, 4),
    }
