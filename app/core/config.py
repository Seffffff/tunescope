"""
Application configuration using pydantic-settings.
All sensitive values are loaded from environment variables.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    app_name: str = "Tunescope"
    debug: bool = False
    secret_key: str

    # Database
    database_url: str  # asyncpg URL for async SQLAlchemy
    database_url_sync: str = ""  # Used by Alembic (sync driver)

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Spotify OAuth
    spotify_client_id: str
    spotify_client_secret: str
    spotify_redirect_uri: str = "http://localhost:8000/auth/callback"
    spotify_scopes: str = "user-read-private playlist-read-private"

    # Ingestion settings
    spotify_api_base: str = "https://api.spotify.com/v1"
    spotify_accounts_base: str = "https://accounts.spotify.com"
    audio_features_batch_size: int = 100  # Spotify's max per request
    rate_limit_retry_max: int = 5
    rate_limit_retry_base_delay: float = 1.0  # seconds

    # Cache TTLs (seconds)
    cache_ttl_playlists: int = 300  # 5 minutes
    cache_ttl_audio_features: int = 3600  # 1 hour

    model_config = {"env_file": ".env", "case_sensitive": False}

    def model_post_init(self, __context):
        # Derive sync URL from async URL if not explicitly set
        if not self.database_url_sync:
            object.__setattr__(
                self,
                "database_url_sync",
                self.database_url.replace("asyncpg", "psycopg2"),
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
