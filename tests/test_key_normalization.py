"""
Tests for app.algorithms.key_normalization.

Pure-function module, no fixtures/mocking needed.
"""

import pytest

from app.algorithms.key_normalization import (
    circle_of_fifths_distance,
    key_to_int,
    normalize_key,
    transpose_key,
)


class TestNormalizeKey:
    def test_sharp_notation_default(self):
        assert normalize_key(8, 1) == ("G#", "major")

    def test_flat_notation_when_requested(self):
        assert normalize_key(8, 1, prefer_flats=True) == ("Ab", "major")

    def test_minor_mode(self):
        assert normalize_key(8, 0) == ("G#", "minor")

    def test_c_major(self):
        assert normalize_key(0, 1) == ("C", "major")

    def test_b_wraps_correctly(self):
        assert normalize_key(11, 1) == ("B", "major")

    @pytest.mark.parametrize("key", [None, -1, -5])
    def test_undetected_key_returns_none_tuple(self, key):
        assert normalize_key(key, 1) == (None, None)

    def test_unknown_mode_returns_none_mode_name(self):
        key_name, mode_name = normalize_key(0, None)
        assert key_name == "C"
        assert mode_name is None

    def test_key_out_of_range_wraps_via_modulo(self):
        # 12 % 12 == 0 == C
        assert normalize_key(12, 1) == ("C", "major")


class TestTransposeKey:
    def test_simple_transpose_up(self):
        assert transpose_key(0, 2) == 2  # C -> D

    def test_wraps_around_top(self):
        assert transpose_key(11, 1) == 0  # B -> C

    def test_wraps_around_negative(self):
        assert transpose_key(5, -7) == 10  # F -> Bb/A#

    def test_zero_semitones_is_identity(self):
        assert transpose_key(6, 0) == 6

    def test_full_octave_is_identity(self):
        assert transpose_key(3, 12) == 3


class TestKeyToInt:
    def test_sharp_name(self):
        assert key_to_int("G#") == 8

    def test_flat_name(self):
        assert key_to_int("Ab") == 8

    def test_natural_name(self):
        assert key_to_int("C") == 0

    def test_is_case_insensitive(self):
        assert key_to_int("g#") == 8

    def test_strips_whitespace(self):
        assert key_to_int("  C  ") == 0

    def test_unknown_key_raises_value_error(self):
        with pytest.raises(ValueError):
            key_to_int("H")

    def test_round_trips_with_normalize_key(self):
        for k in range(12):
            name, _ = normalize_key(k, 1)
            assert key_to_int(name) == k


class TestCircleOfFifthsDistance:
    def test_same_key_is_zero(self):
        assert circle_of_fifths_distance(0, 0) == 0

    def test_adjacent_keys_c_and_g(self):
        assert circle_of_fifths_distance(0, 7) == 1

    def test_opposite_keys_are_max_distance(self):
        assert circle_of_fifths_distance(0, 6) == 6

    def test_symmetric(self):
        assert circle_of_fifths_distance(3, 9) == circle_of_fifths_distance(9, 3)

    def test_distance_is_always_within_bounds(self):
        for a in range(12):
            for b in range(12):
                d = circle_of_fifths_distance(a, b)
                assert 0 <= d <= 6
