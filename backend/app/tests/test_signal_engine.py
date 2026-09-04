"""
Unit tests for the pure signal/attention scoring engine
(app/services/signal_engine.py).

Pure in-memory tests -- no database, no network, no randomness. Every
test uses fixed, hand-picked inputs so results are fully reproducible.
"""

from app.services.signal_engine import (
    Confidence,
    Direction,
    SignalEngineConfig,
    SignalEngineInput,
    analyze_attention,
)


def _base_input(**overrides) -> SignalEngineInput:
    """A 'normal, healthy data' baseline that individual tests can tweak."""
    defaults = dict(
        symbol="TCS",
        current_price=100.0,
        previous_price=100.0,
        current_volume=1_000_000,
        avg_return=0.0,
        return_variance=0.0004,  # std dev = 0.02
        avg_volume=1_000_000,
        benchmark_return=0.0,
        week52_high=150.0,
        week52_low=80.0,
        history_points=60,
    )
    defaults.update(overrides)
    return SignalEngineInput(**defaults)


# --------------------------------------------------------------------------- #
# Core behavior
# --------------------------------------------------------------------------- #
def test_normal_movement_produces_a_low_score():
    """No price/volume/relative anomaly at all -> low attention, NEUTRAL."""
    data = _base_input(current_price=100.2, previous_price=100.0)  # tiny 0.2% move
    result = analyze_attention(data)

    assert result.attention_score < 20
    assert result.direction == Direction.NEUTRAL.value


def test_large_positive_price_movement_produces_high_price_score_and_up():
    # return = +8%, avg_return=0, std=2% -> z = +4 (saturates at 3.0)
    data = _base_input(current_price=108.0, previous_price=100.0)
    result = analyze_attention(data)

    assert result.price_score == 100.0
    assert result.direction == Direction.UP.value


def test_large_negative_price_movement_produces_high_price_score_and_down():
    # return = -8% -> z = -4 (saturates at 3.0 in magnitude)
    data = _base_input(current_price=92.0, previous_price=100.0)
    result = analyze_attention(data)

    assert result.price_score == 100.0
    assert result.direction == Direction.DOWN.value


def test_high_volume_increases_volume_score():
    low_volume = _base_input(current_volume=1_000_000)
    high_volume = _base_input(current_volume=6_000_000)  # 6x average

    low_result = analyze_attention(low_volume)
    high_result = analyze_attention(high_volume)

    assert high_result.volume_score > low_result.volume_score
    assert high_result.volume_score == 100.0  # >= 5x saturation


def test_relative_outperformance_increases_relative_score():
    in_line = _base_input(
        current_price=101.0, previous_price=100.0, benchmark_return=0.01
    )
    outperforming = _base_input(
        current_price=105.0, previous_price=100.0, benchmark_return=0.0
    )

    in_line_result = analyze_attention(in_line)
    outperforming_result = analyze_attention(outperforming)

    assert outperforming_result.relative_score > in_line_result.relative_score


def test_combined_anomalies_produce_a_high_attention_score():
    data = _base_input(
        current_price=112.0,  # big positive move
        previous_price=100.0,
        current_volume=8_000_000,  # 8x average volume
        benchmark_return=-0.02,  # strong outperformance vs benchmark
        week52_high=112.5,  # near the 52-week high too
    )
    result = analyze_attention(data)

    assert result.attention_score >= 70
    assert result.direction == Direction.UP.value
    assert len(result.reasons) >= 3


# --------------------------------------------------------------------------- #
# Bounds
# --------------------------------------------------------------------------- #
def test_score_is_always_between_0_and_100():
    extreme_cases = [
        _base_input(current_price=1000.0, previous_price=1.0, current_volume=10**9),
        _base_input(current_price=0.01, previous_price=1000.0, current_volume=0),
        _base_input(current_price=100.0, previous_price=100.0),
        _base_input(return_variance=-5.0),  # invalid variance
        _base_input(avg_volume=-1.0),  # invalid avg volume
    ]
    for data in extreme_cases:
        result = analyze_attention(data)
        assert 0.0 <= result.attention_score <= 100.0
        assert 0.0 <= result.price_score <= 100.0
        assert 0.0 <= result.volume_score <= 100.0
        assert 0.0 <= result.relative_score <= 100.0
        assert 0.0 <= result.week52_score <= 100.0


# --------------------------------------------------------------------------- #
# Edge cases: invalid / missing statistics
# --------------------------------------------------------------------------- #
def test_zero_standard_deviation_is_handled():
    data = _base_input(
        current_price=105.0, previous_price=100.0, return_variance=0.0, return_std_dev=0.0
    )
    result = analyze_attention(data)

    # Falls back to raw-return scoring rather than dividing by zero.
    assert 0.0 <= result.price_score <= 100.0
    assert result.confidence == Confidence.LOW.value
    assert any("volatility" in note for note in result.data_quality_notes)


def test_negative_variance_is_treated_as_invalid_and_handled():
    data = _base_input(current_price=105.0, previous_price=100.0, return_variance=-0.01)
    result = analyze_attention(data)

    assert 0.0 <= result.price_score <= 100.0
    assert result.confidence == Confidence.LOW.value


def test_missing_previous_price_does_not_crash():
    data = _base_input(previous_price=0)
    result = analyze_attention(data)

    assert result.price_score == 0.0
    assert result.direction == Direction.NEUTRAL.value
    assert any("previous_price" in note for note in result.data_quality_notes)


def test_missing_current_price_does_not_crash():
    data = _base_input(current_price=None)
    result = analyze_attention(data)

    assert result.price_score == 0.0
    assert result.week52_score == 0.0
    assert result.direction == Direction.NEUTRAL.value


def test_zero_or_invalid_average_volume_is_handled():
    for invalid_avg_volume in (0, -100, None):
        data = _base_input(avg_volume=invalid_avg_volume)
        result = analyze_attention(data)

        assert result.volume_score == 0.0
        assert any("avg_volume" in note for note in result.data_quality_notes)


def test_missing_benchmark_is_handled():
    data = _base_input(
        current_price=105.0, previous_price=100.0, benchmark_return=None
    )
    result = analyze_attention(data)

    assert result.relative_score == 0.0
    assert any("benchmark_return" in note for note in result.data_quality_notes)
    # Missing benchmark alone shouldn't be treated as a critical failure.
    assert result.confidence in (Confidence.MEDIUM.value, Confidence.HIGH.value)


def test_missing_week52_range_is_handled():
    data = _base_input(week52_high=None, week52_low=None)
    result = analyze_attention(data)

    assert result.week52_score == 0.0
    assert any("week52" in note for note in result.data_quality_notes)


def test_inconsistent_week52_range_is_handled():
    data = _base_input(week52_high=80.0, week52_low=150.0)  # swapped
    result = analyze_attention(data)

    assert result.week52_score == 0.0


def test_insufficient_history_lowers_confidence():
    plenty_of_history = _base_input(history_points=60)
    barely_any_history = _base_input(history_points=3)

    rich_result = analyze_attention(plenty_of_history)
    sparse_result = analyze_attention(barely_any_history)

    assert rich_result.confidence == Confidence.HIGH.value
    assert sparse_result.confidence == Confidence.LOW.value
    assert any("historical observation" in note for note in sparse_result.data_quality_notes)


def test_none_history_points_does_not_penalize_confidence():
    data = _base_input(history_points=None)
    result = analyze_attention(data)

    assert result.confidence == Confidence.HIGH.value


# --------------------------------------------------------------------------- #
# Reasons / explanation content
# --------------------------------------------------------------------------- #
def test_reasons_and_explanation_are_populated_for_a_significant_move():
    data = _base_input(current_price=110.0, previous_price=100.0, current_volume=4_000_000)
    result = analyze_attention(data)

    assert len(result.reasons) >= 1
    codes = {reason["code"] for reason in result.reasons}
    assert "PRICE_ANOMALY" in codes
    assert "VOLUME_ANOMALY" in codes

    assert isinstance(result.explanation, str)
    assert data.symbol in result.explanation
    assert "score" in result.explanation.lower()


def test_explanation_notes_reduced_confidence():
    data = _base_input(avg_volume=None)  # forces confidence below HIGH
    result = analyze_attention(data)

    assert result.confidence != Confidence.HIGH.value
    assert "confidence" in result.explanation.lower()


def test_no_signal_case_has_a_placeholder_reason():
    data = _base_input(
        current_price=100.0,
        previous_price=100.0,
        current_volume=1_000_000,
        avg_volume=1_000_000,
        benchmark_return=0.0,
        week52_high=None,
        week52_low=None,
    )
    result = analyze_attention(data)

    codes = {reason["code"] for reason in result.reasons}
    assert "NO_SIGNIFICANT_SIGNAL" in codes


def test_reasons_are_json_serializable_dicts():
    data = _base_input(current_price=110.0, previous_price=100.0)
    result = analyze_attention(data)

    for reason in result.reasons:
        assert isinstance(reason, dict)
        assert "code" in reason and "message" in reason and "value" in reason


# --------------------------------------------------------------------------- #
# Direction gating
# --------------------------------------------------------------------------- #
def test_small_price_move_below_significance_threshold_is_neutral_even_if_nonzero():
    # return = +0.3%, std=2% -> z=0.15, well under the significance threshold
    data = _base_input(current_price=100.3, previous_price=100.0)
    result = analyze_attention(data)

    assert result.direction == Direction.NEUTRAL.value
    assert result.price_score < SignalEngineConfig.DIRECTION_PRICE_SCORE_THRESHOLD


def test_direction_depends_only_on_price_not_volume_or_relative():
    """A huge volume spike with a flat price should stay NEUTRAL."""
    data = _base_input(
        current_price=100.0,
        previous_price=100.0,
        current_volume=9_000_000,
        benchmark_return=-0.05,
    )
    result = analyze_attention(data)

    assert result.direction == Direction.NEUTRAL.value
    assert result.volume_score > 0
