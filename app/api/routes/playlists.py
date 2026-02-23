"""
Playlist routes.
"""
from fastapi import APIRouter, Depends, Header, HTTPException

from app.api.schemas import PlaylistListResponse, PlaylistSummary
from app.cache.redis_cache import get_cached_playlists, set_cached_playlists
from app.core.logging import get_logger
from app.ingestion.spotify_client import SpotifyClient

router = APIRouter(prefix="/playlists", tags=["playlists"])
logger = get_logger(__name__)


def _get_access_token(authorization: str = Header(...)) -> str:
    """Extract Bearer token from Authorization header."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    return authorization[len("Bearer "):]


@router.get("", response_model=PlaylistListResponse)
async def list_playlists(
    access_token: str = Depends(_get_access_token),
):
    """
    Fetch all playlists for the authenticated Spotify user.
    Results are cached in Redis to avoid redundant API calls.
    """
    async with SpotifyClient(access_token) as client:
        # Check cache first
        user_profile = await client.get_current_user()
        user_id = user_profile["id"]

        cached = await get_cached_playlists(user_id)
        if cached:
            logger.info("playlists_cache_hit", user_id=user_id)
            return PlaylistListResponse(
                playlists=[PlaylistSummary(**p) for p in cached],
                total=len(cached),
            )

        # Cache miss: fetch from Spotify
        raw_playlists = await client.get_all_user_playlists()

        summaries = [
            PlaylistSummary(
                spotify_id=pl["id"],
                name=pl.get("name", ""),
                description=pl.get("description"),
                track_count=(pl.get("tracks") or {}).get("total", 0),
                snapshot_id=pl.get("snapshot_id"),
            )
            for pl in raw_playlists
        ]

        # Populate cache
        await set_cached_playlists(user_id, [s.model_dump() for s in summaries])

        return PlaylistListResponse(playlists=summaries, total=len(summaries))
