"""
Auth routes: Spotify OAuth2 Authorization Code flow.

Flow:
  1. GET /auth/login  → redirect user to Spotify consent screen
  2. Spotify redirects to GET /auth/callback?code=...
  3. We exchange the code for tokens, upsert the user
  4. Redirect to the frontend /app?token=xxx
"""

import urllib.parse
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.repositories.repo import upsert_user
from app.db.session import get_db
from app.ingestion.spotify_client import SpotifyClient

router = APIRouter(prefix="/auth", tags=["auth"])
logger = get_logger(__name__)
settings = get_settings()


@router.get("/login")
async def login():
    """Redirect the user to Spotify's OAuth authorization page."""
    params = {
        "client_id": settings.spotify_client_id,
        "response_type": "code",
        "redirect_uri": settings.spotify_redirect_uri,
        "scope": settings.spotify_scopes,
        "show_dialog": "false",
    }
    auth_url = f"{settings.spotify_accounts_base}/authorize?" + urllib.parse.urlencode(params)
    logger.info("oauth_redirect_initiated")
    return RedirectResponse(url=auth_url)


@router.get("/callback")
async def callback(
    code: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Handle Spotify's redirect after user authorizes.
    Exchanges the authorization code for tokens, upserts the user,
    then redirects to the frontend with the token in the query string.
    """
    if error:
        return RedirectResponse(url=f"/app?error={error}")

    if not code:
        return RedirectResponse(url="/app?error=missing_code")

    # Exchange code for tokens
    async with SpotifyClient("") as client:
        try:
            token_data = await client.exchange_code_for_tokens(
                code=code,
                redirect_uri=settings.spotify_redirect_uri,
            )
        except Exception as exc:
            logger.error("token_exchange_failed", error=str(exc))
            return RedirectResponse(url="/app?error=token_exchange_failed")

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 3600)
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

    # Fetch the user's Spotify profile
    async with SpotifyClient(access_token) as client:
        profile = await client.get_current_user()

    spotify_id = profile["id"]

    # Upsert user in our DB
    await upsert_user(
        db,
        spotify_id=spotify_id,
        display_name=profile.get("display_name"),
        email=profile.get("email"),
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=expires_at,
    )

    logger.info("user_authenticated", spotify_id=spotify_id)

    # Redirect to frontend with token — frontend stores it in sessionStorage
    return RedirectResponse(url=f"/app?token={access_token}")
