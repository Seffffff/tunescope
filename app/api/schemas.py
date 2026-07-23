"""
Pydantic schemas for API request/response validation.
Separate from ORM models — these define the API contract.
"""

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    spotify_user_id: str
    display_name: str | None


# ---------------------------------------------------------------------------
# Playlists
# ---------------------------------------------------------------------------


class PlaylistSummary(BaseModel):
    spotify_id: str
    name: str
    description: str | None = None
    track_count: int
    snapshot_id: str | None = None


class PlaylistListResponse(BaseModel):
    playlists: list[PlaylistSummary]
    total: int


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


class IngestionRequest(BaseModel):
    force_refetch: bool = Field(
        default=False,
        description="If True, re-ingest even if snapshot_id matches",
    )


class IngestionResponse(BaseModel):
    playlist_id: str
    tracks_upserted: int
    audio_features_upserted: int
    audio_features_skipped_no_preview: int = 0
    audio_features_needed: int = 0
    audio_features_from_cache: int = 0
    audio_features_rb_returned: int = 0
    audio_features_not_found: int = 0
    elapsed_seconds: float = 0.0
    error_count: int
    errors: list[str] = []
    skipped: bool = False
    skip_reason: str | None = None


# ---------------------------------------------------------------------------
# Playlist track listing (with feature status)
# ---------------------------------------------------------------------------


class TrackWithFeatures(BaseModel):
    spotify_id: str
    name: str
    artist: str
    album: str | None = None
    duration_ms: int
    tempo: float | None = None
    key: int | None = None
    key_name: str | None = None
    mode: int | None = None
    mode_name: str | None = None
    energy: float | None = None
    danceability: float | None = None
    valence: float | None = None
    analysis_source: str | None = None
    position: int | None = None


class TrackWithoutFeatures(BaseModel):
    spotify_id: str
    name: str
    artist: str
    album: str | None = None
    duration_ms: int
    position: int | None = None


class PlaylistTracksResponse(BaseModel):
    playlist_id: str
    total: int
    analyzed_count: int
    missing_count: int
    analyzed: list[TrackWithFeatures]
    missing: list[TrackWithoutFeatures]


# ---------------------------------------------------------------------------
# YouTube / librosa fallback analysis
# ---------------------------------------------------------------------------


class TrackAnalysisRequest(BaseModel):
    spotify_id: str
    name: str
    artist: str


class YouTubeAnalysisRequest(BaseModel):
    tracks: list[TrackAnalysisRequest]


class TrackAnalysisResult(BaseModel):
    spotify_id: str
    name: str
    artist: str
    status: str  # "success" | "not_found" | "db_error"
    error: str | None = None
    tempo: float | None = None
    key: int | None = None
    mode: int | None = None
    energy: float | None = None
    analysis_source: str | None = None  # "reccobeats_upload" | "youtube_librosa"


class YouTubeAnalysisResponse(BaseModel):
    playlist_id: str
    attempted: int
    succeeded: int
    failed: int
    results: list[TrackAnalysisResult]


# ---------------------------------------------------------------------------
# Audio Features / Tracks
# ---------------------------------------------------------------------------


class AudioFeaturesResponse(BaseModel):
    track_spotify_id: str
    tempo: float | None
    key: int | None
    key_name: str | None
    mode: int | None
    mode_name: str | None
    time_signature: int | None
    energy: float | None
    danceability: float | None
    valence: float | None


# ---------------------------------------------------------------------------
# Algorithm Outputs
# ---------------------------------------------------------------------------


class CompatibilityScore(BaseModel):
    track_a_id: str
    track_b_id: str
    score: float = Field(..., ge=0.0, le=100.0)
    interpretation: str


class KeyNormalizationResult(BaseModel):
    spotify_id: str
    original_key: int
    original_mode: int
    key_name: str | None
    mode_name: str | None
    transposed_key: int | None = None
    transposed_key_name: str | None = None


class BPMDriftResponse(BaseModel):
    spotify_id: str
    spotify_bpm: float
    estimated_bpm: float
    deviation_pct: float
    flagged: bool
    flag_reason: str | None
