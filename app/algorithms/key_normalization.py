"""
Key Normalization Algorithm
===========================
Spotify represents musical key as an integer 0–11 using Pitch Class notation
and mode as 0 (minor) or 1 (major).

This module provides:
  - normalize_key()     : integer → human-readable (e.g., 9 + major → "A major")
  - transpose_key()     : shift a key by N semitones
  - key_to_int()        : reverse lookup from name to integer
  - circle_of_fifths_distance() : used by harmonic scoring
"""
from __future__ import annotations

# Pitch class notation: index 0=C, 1=C#/Db, ..., 11=B
_SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_FLAT_NAMES  = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

# Circle of fifths order (major) for distance calculations
_CIRCLE_OF_FIFTHS = [0, 7, 2, 9, 4, 11, 6, 1, 8, 3, 10, 5]
_COF_POSITION = {k: i for i, k in enumerate(_CIRCLE_OF_FIFTHS)}


def normalize_key(
    key: int | None,
    mode: int | None,
    prefer_flats: bool = False,
) -> tuple[str | None, str | None]:
    """
    Convert Spotify's numeric key + mode into human-readable strings.

    Args:
        key:         Spotify key integer (0–11), or -1/None if undetected
        mode:        0 = minor, 1 = major
        prefer_flats: if True, use flat notation (Ab) instead of sharps (G#)

    Returns:
        (key_name, mode_name) e.g. ("Ab", "major") or (None, None)

    Example:
        normalize_key(8, 1, prefer_flats=True)  → ("Ab", "major")
        normalize_key(8, 0)                      → ("G#", "minor")
    """
    if key is None or key < 0:
        return None, None

    names = _FLAT_NAMES if prefer_flats else _SHARP_NAMES
    key_name = names[key % 12]
    mode_name = "major" if mode == 1 else "minor" if mode == 0 else None
    return key_name, mode_name


def transpose_key(original_key: int, semitones: int) -> int:
    """
    Transpose a key by a number of semitones (positive = up, negative = down).
    Result is always in range 0–11.

    Example:
        transpose_key(0, 2)   → 2   (C → D)
        transpose_key(11, 1)  → 0   (B → C, wraps around)
        transpose_key(5, -7)  → 10  (F → Bb/A#)
    """
    return (original_key + semitones) % 12


def key_to_int(key_name: str) -> int:
    """
    Reverse lookup: key name string → pitch class integer.
    Supports both sharp and flat notation.

    Raises ValueError if key_name is not recognized.
    """
    normalized = key_name.strip().capitalize()
    if normalized in _SHARP_NAMES:
        return _SHARP_NAMES.index(normalized)
    if normalized in _FLAT_NAMES:
        return _FLAT_NAMES.index(normalized)
    raise ValueError(f"Unknown key name: '{key_name}'")


def circle_of_fifths_distance(key_a: int, key_b: int) -> int:
    """
    Return the shortest distance between two keys on the circle of fifths.
    Result is in range 0–6 (0 = same key, 6 = tritone/most distant).

    This is used by the harmonic compatibility scorer.

    Example:
        circle_of_fifths_distance(0, 7)  → 1  (C and G are adjacent)
        circle_of_fifths_distance(0, 6)  → 6  (C and F# are opposite)
    """
    pos_a = _COF_POSITION[key_a % 12]
    pos_b = _COF_POSITION[key_b % 12]
    diff = abs(pos_a - pos_b)
    return min(diff, 12 - diff)  # Shortest path around the circle
