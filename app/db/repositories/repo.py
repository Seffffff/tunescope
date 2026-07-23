"""
Repository layer: all database read/write operations live here.

Design decision: Repositories receive a session from the caller rather than
creating their own — this keeps transaction boundaries in the service/ingestion
layer where business logic lives.
"""

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AudioFeatures,
    Playlist,
    PlaylistTrack,
    RawSpotifyPayload,
    Track,
    User,
)

# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


async def upsert_user(session: AsyncSession, spotify_id: str, **kwargs) -> User:
    """
    Idempotent upsert: create or update user by spotify_id.
    Returns the persisted User instance.
    """
    stmt = (
        pg_insert(User)
        .values(spotify_id=spotify_id, **kwargs)
        .on_conflict_do_update(
            index_elements=["spotify_id"],
            set_={k: v for k, v in kwargs.items()},
        )
        .returning(User)
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.scalar_one()


async def get_user_by_spotify_id(session: AsyncSession, spotify_id: str) -> User | None:
    result = await session.execute(select(User).where(User.spotify_id == spotify_id))
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Playlist
# ---------------------------------------------------------------------------


async def upsert_playlist(
    session: AsyncSession, spotify_id: str, owner_id: str, **kwargs
) -> Playlist:
    stmt = (
        pg_insert(Playlist)
        .values(spotify_id=spotify_id, owner_id=owner_id, **kwargs)
        .on_conflict_do_update(
            index_elements=["spotify_id"],
            set_={k: v for k, v in kwargs.items()},
        )
        .returning(Playlist)
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.scalar_one()


async def get_playlist_by_spotify_id(session: AsyncSession, spotify_id: str) -> Playlist | None:
    result = await session.execute(select(Playlist).where(Playlist.spotify_id == spotify_id))
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Track
# ---------------------------------------------------------------------------


async def upsert_track(session: AsyncSession, spotify_id: str, **kwargs) -> Track:
    stmt = (
        pg_insert(Track)
        .values(spotify_id=spotify_id, **kwargs)
        .on_conflict_do_update(
            index_elements=["spotify_id"],
            set_={k: v for k, v in kwargs.items()},
        )
        .returning(Track)
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.scalar_one()


async def get_tracks_missing_audio_features(
    session: AsyncSession, spotify_ids: list[str]
) -> list[str]:
    """Return subset of spotify_ids that have no usable audio features stored.
    A row with a NULL tempo is treated as missing — it means a previous run
    created an empty shell that was never populated by ReccoBeats.
    """
    result = await session.execute(
        select(Track.spotify_id)
        .join(AudioFeatures, AudioFeatures.track_id == Track.id, isouter=True)
        .where(
            Track.spotify_id.in_(spotify_ids),
            or_(AudioFeatures.id.is_(None), AudioFeatures.tempo.is_(None)),
        )
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# PlaylistTrack
# ---------------------------------------------------------------------------


async def upsert_playlist_track(
    session: AsyncSession,
    playlist_id: str,
    track_id: str,
    position: int,
    added_at: datetime | None,
) -> None:
    stmt = (
        pg_insert(PlaylistTrack)
        .values(
            playlist_id=playlist_id,
            track_id=track_id,
            position=position,
            added_at=added_at,
        )
        .on_conflict_do_update(
            constraint="uq_playlist_track",
            set_={"position": position, "added_at": added_at},
        )
    )
    await session.execute(stmt)


# ---------------------------------------------------------------------------
# AudioFeatures
# ---------------------------------------------------------------------------


async def upsert_audio_features(session: AsyncSession, track_id: str, **kwargs) -> AudioFeatures:
    stmt = (
        pg_insert(AudioFeatures)
        .values(track_id=track_id, **kwargs)
        .on_conflict_do_update(
            index_elements=["track_id"],
            set_={k: v for k, v in kwargs.items()},
        )
        .returning(AudioFeatures)
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.scalar_one()


# ---------------------------------------------------------------------------
# Raw payloads (append-only)
# ---------------------------------------------------------------------------


async def store_raw_payload(
    session: AsyncSession,
    entity_type: str,
    spotify_id: str,
    payload: dict,
) -> None:
    raw = RawSpotifyPayload(entity_type=entity_type, spotify_id=spotify_id, payload=payload)
    session.add(raw)
    await session.flush()
