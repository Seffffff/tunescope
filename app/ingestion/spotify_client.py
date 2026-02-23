"""
Spotify API client.

Handles:
- Access token injection
- 429 rate limit detection with Retry-After header
- Exponential backoff via tenacity
- Batch audio feature fetching (100 IDs per call)
"""
import asyncio
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


class SpotifyRateLimitError(Exception):
    """Raised when Spotify returns HTTP 429."""
    def __init__(self, retry_after: int = 1):
        self.retry_after = retry_after
        super().__init__(f"Rate limited. Retry after {retry_after}s")


class SpotifyAPIError(Exception):
    """General Spotify API error."""


class SpotifyClient:
    def __init__(self, access_token: str):
        self.access_token = access_token
        self._client = httpx.AsyncClient(
            base_url=settings.spotify_api_base,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self._client.aclose()

    async def _get(self, url: str, params: dict | None = None) -> dict:
        """
        Core GET with rate-limit handling.
        On 429, respects Retry-After header before raising SpotifyRateLimitError
        so tenacity can retry the whole call.
        """
        response = await self._client.get(url, params=params)

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 1))
            logger.warning("spotify_rate_limited", retry_after=retry_after, url=url)
            await asyncio.sleep(retry_after)
            raise SpotifyRateLimitError(retry_after)

        if response.status_code == 401:
            raise SpotifyAPIError("Access token expired or invalid")

        if not response.is_success:
            raise SpotifyAPIError(
                f"Spotify API error {response.status_code}: {response.text}"
            )

        return response.json()

    @retry(
        retry=retry_if_exception_type(SpotifyRateLimitError),
        stop=stop_after_attempt(settings.rate_limit_retry_max),
        wait=wait_exponential(
            multiplier=settings.rate_limit_retry_base_delay, min=1, max=30
        ),
        reraise=True,
    )
    async def get_current_user(self) -> dict:
        return await self._get("/me")

    @retry(
        retry=retry_if_exception_type(SpotifyRateLimitError),
        stop=stop_after_attempt(settings.rate_limit_retry_max),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )
    async def get_user_playlists(self, limit: int = 50, offset: int = 0) -> dict:
        return await self._get("/me/playlists", params={"limit": limit, "offset": offset})

    async def get_all_user_playlists(self) -> list[dict]:
        """Paginate through all playlists for the authenticated user."""
        all_playlists: list[dict] = []
        offset = 0
        limit = 50

        while True:
            page = await self.get_user_playlists(limit=limit, offset=offset)
            items = page.get("items", [])
            all_playlists.extend(items)
            if len(items) < limit or not page.get("next"):
                break
            offset += limit

        logger.info("fetched_all_playlists", count=len(all_playlists))
        return all_playlists

    @retry(
        retry=retry_if_exception_type(SpotifyRateLimitError),
        stop=stop_after_attempt(settings.rate_limit_retry_max),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )
    async def get_playlist_tracks(
        self, playlist_id: str, limit: int = 100, offset: int = 0
    ) -> dict:
        return await self._get(
            f"/playlists/{playlist_id}/tracks",
            params={"limit": limit, "offset": offset},
        )

    async def get_all_playlist_tracks(self, playlist_id: str) -> list[dict]:
        """Paginate through all tracks in a playlist."""
        all_items: list[dict] = []
        offset = 0
        limit = 100

        while True:
            page = await self.get_playlist_tracks(playlist_id, limit=limit, offset=offset)
            items = [item for item in page.get("items", []) if item.get("track")]
            all_items.extend(items)
            if len(items) < limit or not page.get("next"):
                break
            offset += limit

        logger.info("fetched_playlist_tracks", playlist_id=playlist_id, count=len(all_items))
        return all_items

    async def exchange_code_for_tokens(
        self, code: str, redirect_uri: str
    ) -> dict[str, Any]:
        """OAuth2 authorization code exchange."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.spotify_accounts_base}/api/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": settings.spotify_client_id,
                    "client_secret": settings.spotify_client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            return response.json()
