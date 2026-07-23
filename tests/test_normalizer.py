"""
Tests for app.transformation.normalizer.

Pure-function module: raw Spotify/librosa dicts in, normalized domain dicts out.
"""

from datetime import UTC

import pytest

from app.transformation.normalizer import (
    parse_added_at,
    transform_audio_features,
    transform_playlist,
    transform_track,
)


class TestTransformTrack:
    def test_full_track_item(self):
        raw_item = {
            "track": {
                "id": "abc123",
                "name": "Song Name",
                "artists": [{"name": "Artist One"}, {"name": "Artist Two"}],
                "album": {"name": "Album Name"},
                "duration_ms": 210000,
                "explicit": True,
                "popularity": 72,
            }
        }
        result = transform_track(raw_item)
        assert result == {
            "spotify_id": "abc123",
            "name": "Song Name",
            "artist": "Artist One",  # only the primary/first artist
            "album": "Album Name",
            "duration_ms": 210000,
            "explicit": True,
            "popularity": 72,
        }

    def test_missing_artists_defaults_to_unknown(self):
        raw_item = {"track": {"id": "x", "artists": []}}
        result = transform_track(raw_item)
        assert result["artist"] == "Unknown"

    def test_missing_name_defaults_to_unknown(self):
        raw_item = {"track": {"id": "x", "artists": [{"name": "A"}]}}
        result = transform_track(raw_item)
        assert result["name"] == "Unknown"

    def test_missing_album_is_none(self):
        raw_item = {"track": {"id": "x", "artists": [{"name": "A"}]}}
        result = transform_track(raw_item)
        assert result["album"] is None

    def test_null_track_field_raises_key_error(self):
        # Spotify returns track: null for locally-added/removed playlist items.
        # transform_track expects the caller to have already filtered these out
        # (see ingestion_service.ingest_playlist: `if not raw_item.get("track"): continue`)
        # -- this test documents that precondition rather than silently passing bad data through.
        raw_item = {"track": None}
        with pytest.raises(KeyError):
            transform_track(raw_item)

    def test_missing_duration_defaults_to_zero(self):
        raw_item = {"track": {"id": "x", "artists": []}}
        result = transform_track(raw_item)
        assert result["duration_ms"] == 0

    def test_missing_explicit_defaults_to_false(self):
        raw_item = {"track": {"id": "x", "artists": []}}
        result = transform_track(raw_item)
        assert result["explicit"] is False


class TestTransformAudioFeatures:
    def test_full_features(self):
        raw = {
            "tempo": 128.0,
            "key": 8,
            "mode": 1,
            "time_signature": 4,
            "energy": 0.8,
            "danceability": 0.7,
            "valence": 0.5,
            "acousticness": 0.1,
            "instrumentalness": 0.0,
            "liveness": 0.2,
            "loudness": -5.0,
            "speechiness": 0.05,
            "analysis_source": "reccobeats",
        }
        result = transform_audio_features(raw)
        assert result["tempo"] == 128.0
        assert result["key"] == 8
        assert result["mode"] == 1
        assert result["key_name"] == "G#"
        assert result["mode_name"] == "major"

    def test_raw_json_strips_derived_fields(self):
        raw = {"key": 0, "mode": 1, "key_name": "should not appear", "mode_name": "nor this"}
        result = transform_audio_features(raw)
        assert "key_name" not in result["raw_json"]
        assert "mode_name" not in result["raw_json"]

    def test_raw_json_keeps_passthrough_fields(self):
        raw = {"key": 0, "mode": 1, "analysis_source": "librosa"}
        result = transform_audio_features(raw)
        assert result["raw_json"]["analysis_source"] == "librosa"

    def test_missing_key_and_mode_yields_none_names(self):
        result = transform_audio_features({})
        assert result["key_name"] is None
        assert result["mode_name"] is None

    def test_undetected_key_negative_one(self):
        result = transform_audio_features({"key": -1, "mode": 1})
        assert result["key_name"] is None
        assert result["mode_name"] is None


class TestTransformPlaylist:
    def test_full_playlist(self):
        raw = {
            "id": "pl1",
            "name": "My Playlist",
            "description": "desc",
            "snapshot_id": "snap1",
            "tracks": {"total": 42},
        }
        result = transform_playlist(raw, owner_id="user1")
        assert result == {
            "spotify_id": "pl1",
            "owner_id": "user1",
            "name": "My Playlist",
            "description": "desc",
            "snapshot_id": "snap1",
            "track_count": 42,
        }

    def test_missing_name_defaults(self):
        result = transform_playlist({"id": "pl1"}, owner_id="user1")
        assert result["name"] == "Unnamed Playlist"

    def test_missing_tracks_object_defaults_to_zero(self):
        result = transform_playlist({"id": "pl1"}, owner_id="user1")
        assert result["track_count"] == 0

    def test_missing_description_is_none(self):
        result = transform_playlist({"id": "pl1"}, owner_id="user1")
        assert result["description"] is None


class TestParseAddedAt:
    def test_parses_zulu_timestamp(self):
        result = parse_added_at("2024-01-15T10:30:00Z")
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.tzinfo == UTC

    def test_none_input_returns_none(self):
        assert parse_added_at(None) is None

    def test_empty_string_returns_none(self):
        assert parse_added_at("") is None

    def test_malformed_string_returns_none_not_raises(self):
        assert parse_added_at("not-a-date") is None
