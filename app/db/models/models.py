"""
SQLAlchemy ORM models.

Design decisions:
- UUID primary keys avoid conflicts in distributed inserts
- raw_spotify_payloads stores the original API response (audit trail + re-processing)
- JSONB on audio_features.raw_json allows schema evolution without migrations
- snapshot_id on playlists enables idempotent re-ingestion detection
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    spotify_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(256))
    email: Mapped[str | None] = mapped_column(String(256))
    access_token: Mapped[str | None] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    playlists: Mapped[list["Playlist"]] = relationship(back_populates="owner")


class Playlist(Base):
    __tablename__ = "playlists"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    spotify_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # snapshot_id changes whenever the playlist is modified — use to skip re-ingestion
    snapshot_id: Mapped[str | None] = mapped_column(String(256))
    track_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped["User"] = relationship(back_populates="playlists")
    playlist_tracks: Mapped[list["PlaylistTrack"]] = relationship(back_populates="playlist")


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    spotify_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    # Storing primary artist only for simplicity; extend to artists table for full normalization
    artist: Mapped[str] = mapped_column(String(512), nullable=False)
    album: Mapped[str | None] = mapped_column(String(512))
    duration_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    explicit: Mapped[bool] = mapped_column(default=False)
    popularity: Mapped[int | None] = mapped_column(Integer)
    chordify_embed_url: Mapped[str | None] = mapped_column(Text)
    ultimate_guitar_url: Mapped[str | None] = mapped_column(Text) 
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    audio_features: Mapped["AudioFeatures | None"] = relationship(back_populates="track")
    playlist_tracks: Mapped[list["PlaylistTrack"]] = relationship(back_populates="track")


class PlaylistTrack(Base):
    """Junction table preserving playlist ordering and add-date."""
    __tablename__ = "playlist_tracks"
    __table_args__ = (
        UniqueConstraint("playlist_id", "track_id", name="uq_playlist_track"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    playlist_id: Mapped[str] = mapped_column(ForeignKey("playlists.id"), nullable=False)
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    added_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    playlist: Mapped["Playlist"] = relationship(back_populates="playlist_tracks")
    track: Mapped["Track"] = relationship(back_populates="playlist_tracks")


class AudioFeatures(Base):
    __tablename__ = "audio_features"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    track_id: Mapped[str] = mapped_column(
        ForeignKey("tracks.id"), unique=True, nullable=False, index=True
    )
    # Spotify numeric values
    tempo: Mapped[float | None] = mapped_column(Float)
    key: Mapped[int | None] = mapped_column(Integer)   # 0–11 (Pitch Class notation)
    mode: Mapped[int | None] = mapped_column(Integer)  # 0=minor, 1=major
    time_signature: Mapped[int | None] = mapped_column(Integer)
    energy: Mapped[float | None] = mapped_column(Float)
    danceability: Mapped[float | None] = mapped_column(Float)
    valence: Mapped[float | None] = mapped_column(Float)
    acousticness: Mapped[float | None] = mapped_column(Float)
    instrumentalness: Mapped[float | None] = mapped_column(Float)
    liveness: Mapped[float | None] = mapped_column(Float)
    loudness: Mapped[float | None] = mapped_column(Float)
    speechiness: Mapped[float | None] = mapped_column(Float)
    # Derived / normalized fields
    key_name: Mapped[str | None] = mapped_column(String(16))     # e.g. "Ab", "F#"
    mode_name: Mapped[str | None] = mapped_column(String(8))     # "major" / "minor"
    # Full raw payload for schema evolution without data loss
    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    track: Mapped["Track"] = relationship(back_populates="audio_features")


class RawSpotifyPayload(Base):
    """
    Append-only audit table storing every raw API response.
    Enables re-processing without hitting the API again.
    """
    __tablename__ = "raw_spotify_payloads"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)  # playlist/track/audio_feature
    spotify_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )