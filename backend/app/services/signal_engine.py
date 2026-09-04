"""
Signal / attention scoring engine for the Smart Market Watchlist.

Pure, deterministic, dependency-free scoring: given a fresh market data
point plus rolling baseline statistics for an instrument, produce an
"attention analysis" describing whether -- and why -- it deserves the
user's attention right now.

Deliberately excluded from this module (by design, not oversight):
  * no database access, no ORM models
  * no API routes
  * no background worker / scheduling
  * no LLM calls -- explanations are built from a fixed template

Same inputs always produce the same output. Nothing here raises on
missing or malformed data; instead, the affected sub-score degrades to
0 and the overall confidence/data-quality rating drops, so a bad tick
can never mask itself as a strong signal with an invented value.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional, Union

Number = Union[int, float, Decimal, None]


# --------------------------------------------------------------------------- #
# Configuration -- every tunable constant lives here so the model can be
# recalibrated later without touching any of the scoring logic below.
# --------------------------------------------------------------------------- #
class SignalEngineConfig:
    # Composite score weights. Must sum to 1.0.
    WEIGHT_PRICE = 0.40
    WEIGHT_VOLUME = 0.25
    WEIGHT_RELATIVE = 0.20
    WEIGHT_WEEK52_PROXIMITY = 0.15

    # --- price anomaly (volatility-normalized z-score) ---
    # |z-score| at/above this saturates the price score at 100.
    PRICE_Z_SCORE_SATURATION = 3.0
    # Used only as a fallback when standard deviation can't be computed
    # (missing/zero/negative variance & std dev): a plain unnormalized
    # percentage move at/above this saturates the fallback score at 100.
    PRICE_FALLBACK_RETURN_SATURATION = 0.05  # 5%

    # --- volume anomaly ---
    # current_volume / avg_volume at/above this saturates the volume score.
    VOLUME_RATIO_SATURATION = 5.0
    # Ratios at/below this are "in line with normal" and contribute nothing.
    VOLUME_RATIO_FLOOR = 1.0

    # --- market-relative movement ---
    # |stock_return - benchmark_return| at/above this saturates the score.
    RELATIVE_RETURN_SATURATION = 0.03  # 3 percentage points

    # --- 52-week range proximity ---
    # Distance to the nearest 52-week extreme, at/below which the
    # proximity score saturates at 100.
    WEEK52_PROXIMITY_SATURATION = 0.02  # within 2%

    # --- direction gating ---
    # price_score must reach this threshold before a movement is called
    # UP/DOWN at all. Below it, direction is always NEUTRAL, regardless
    # of the sign of the return -- significance is judged separately
    # from direction.
    DIRECTION_PRICE_SCORE_THRESHOLD = 30.0

    # --- confidence / data quality ---
    # Fewer historical observations than this behind avg_return /
    # return_variance means "don't fully trust this yet".
    MIN_HISTORY_POINTS_FOR_FULL_CONFIDENCE = 20


class Direction(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    NEUTRAL = "NEUTRAL"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class Reason:
    """One structured, machine-readable contributing factor."""

    code: str
    message: str
    value: Optional[float] = None


@dataclass(frozen=True)
class SignalEngineInput:
    """
    Everything the engine needs for one scoring pass.

    Only current/previous price and current volume are "required" in
    spirit; every baseline statistic is optional. Missing or invalid
    statistics degrade the relevant sub-score and overall confidence
    instead of raising.
    """

    symbol: str
    current_price: Number
    previous_price: Number
    current_volume: Number

    # Baseline return statistics for the price-anomaly z-score.
    avg_return: Number = None
    return_variance: Number = None
    return_std_dev: Number = None  # may be supplied directly instead of variance

    # Baseline volume statistic.
    avg_volume: Number = None

    # Same-period return of a benchmark index, for relative movement.
    benchmark_return: Number = None

    # 52-week range, for proximity-to-extreme scoring.
    week52_high: Number = None
    week52_low: Number = None

    # Number of historical observations backing avg_return /
    # return_variance -- used only to gate confidence, not scoring math.
    history_points: Optional[int] = None


@dataclass(frozen=True)
class AttentionAnalysis:
    """The result of one scoring pass. Fully JSON-serializable via asdict()."""

    attention_score: float
    direction: str
    price_score: float
    volume_score: float
    relative_score: float
    week52_score: float
    confidence: str
    data_quality_notes: list[str]
    reasons: list[dict]
    explanation: str


# --------------------------------------------------------------------------- #
# Small numeric helpers
# --------------------------------------------------------------------------- #
def _to_float(value: Number) -> Optional[float]:
    """Best-effort conversion to float. None / garbage -> None. Never raises."""
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _saturate(magnitude: float, saturation_point: float) -> float:
    """Linearly scale a non-negative magnitude to 0-100, capped at 100."""
    if saturation_point <= 0:
        return 0.0
    return _clip((magnitude / saturation_point) * 100.0)


# --------------------------------------------------------------------------- #
# Sub-score 1: price anomaly
# --------------------------------------------------------------------------- #
def _compute_current_return(
    current_price: Optional[float], previous_price: Optional[float]
) -> tuple[Optional[float], list[str]]:
    notes: list[str] = []
    if current_price is None or current_price <= 0:
        notes.append("current_price is missing or non-positive")
        return None, notes
    if previous_price is None or previous_price <= 0:
        notes.append("previous_price is missing or non-positive")
        return None, notes
    return (current_price - previous_price) / previous_price, notes


def _compute_price_score(
    current_return: Optional[float],
    avg_return: Optional[float],
    variance: Optional[float],
    std_dev: Optional[float],
    cfg: type[SignalEngineConfig],
) -> tuple[float, list[Reason], list[str]]:
    notes: list[str] = []
    reasons: list[Reason] = []

    if current_return is None:
        notes.append("current price return unavailable")
        return 0.0, reasons, notes

    resolved_std = std_dev if std_dev is not None and std_dev > 0 else None
    if resolved_std is None and variance is not None and variance > 0:
        resolved_std = math.sqrt(variance)

    if avg_return is not None and resolved_std is not None:
        z = (current_return - avg_return) / resolved_std
        score = _saturate(abs(z), cfg.PRICE_Z_SCORE_SATURATION)
        if abs(z) > 0:
            reasons.append(
                Reason(
                    code="PRICE_ANOMALY",
                    message=(
                        f"Price movement was {abs(z):.2f}x normal volatility "
                        f"(z-score {z:+.2f})."
                    ),
                    value=z,
                )
            )
        return score, reasons, notes

    # Fallback: no usable historical volatility -- score off the raw
    # return magnitude instead, and flag the reduced confidence.
    notes.append("insufficient volatility history (missing/zero std dev or variance)")
    score = _saturate(abs(current_return), cfg.PRICE_FALLBACK_RETURN_SATURATION)
    if abs(current_return) > 0:
        reasons.append(
            Reason(
                code="PRICE_ANOMALY_LOW_CONFIDENCE",
                message=(
                    f"Price moved {current_return * 100:+.2f}% "
                    "(no volatility baseline available, so this is an "
                    "unnormalized estimate)."
                ),
                value=current_return,
            )
        )
    return score, reasons, notes


# --------------------------------------------------------------------------- #
# Sub-score 2: volume anomaly
# --------------------------------------------------------------------------- #
def _compute_volume_score(
    current_volume: Optional[float],
    avg_volume: Optional[float],
    cfg: type[SignalEngineConfig],
) -> tuple[float, list[Reason], list[str]]:
    notes: list[str] = []
    reasons: list[Reason] = []

    if current_volume is None or current_volume < 0:
        notes.append("current_volume is missing or invalid")
        return 0.0, reasons, notes
    if avg_volume is None or avg_volume <= 0:
        notes.append("avg_volume is missing or non-positive")
        return 0.0, reasons, notes

    ratio = current_volume / avg_volume
    excess = max(0.0, ratio - cfg.VOLUME_RATIO_FLOOR)
    saturation_excess = cfg.VOLUME_RATIO_SATURATION - cfg.VOLUME_RATIO_FLOOR
    score = _saturate(excess, saturation_excess)

    if ratio > cfg.VOLUME_RATIO_FLOOR:
        reasons.append(
            Reason(
                code="VOLUME_ANOMALY",
                message=f"Volume was {ratio:.2f}x the average volume.",
                value=ratio,
            )
        )
    return score, reasons, notes


# --------------------------------------------------------------------------- #
# Sub-score 3: market-relative movement
# --------------------------------------------------------------------------- #
def _compute_relative_score(
    current_return: Optional[float],
    benchmark_return: Optional[float],
    cfg: type[SignalEngineConfig],
) -> tuple[float, list[Reason], list[str]]:
    notes: list[str] = []
    reasons: list[Reason] = []

    if current_return is None:
        return 0.0, reasons, notes
    if benchmark_return is None:
        notes.append("benchmark_return is missing")
        return 0.0, reasons, notes

    diff = current_return - benchmark_return
    score = _saturate(abs(diff), cfg.RELATIVE_RETURN_SATURATION)
    if abs(diff) > 0:
        verb = "outperformed" if diff > 0 else "underperformed"
        reasons.append(
            Reason(
                code="RELATIVE_MOVEMENT",
                message=(
                    f"Stock {verb} the benchmark by {abs(diff) * 100:.2f} "
                    "percentage points."
                ),
                value=diff,
            )
        )
    return score, reasons, notes


# --------------------------------------------------------------------------- #
# Sub-score 4: 52-week range proximity
# --------------------------------------------------------------------------- #
def _compute_week52_score(
    current_price: Optional[float],
    week52_high: Optional[float],
    week52_low: Optional[float],
    cfg: type[SignalEngineConfig],
) -> tuple[float, list[Reason], list[str]]:
    notes: list[str] = []
    reasons: list[Reason] = []

    if current_price is None or current_price <= 0:
        notes.append("current_price unavailable for 52-week proximity check")
        return 0.0, reasons, notes
    if (
        week52_high is None
        or week52_low is None
        or week52_high <= 0
        or week52_low <= 0
    ):
        notes.append("week52_high/week52_low is missing or invalid")
        return 0.0, reasons, notes
    if week52_high < week52_low:
        notes.append("week52_high/week52_low values are inconsistent")
        return 0.0, reasons, notes

    distance_from_high = abs(week52_high - current_price) / week52_high
    distance_from_low = abs(current_price - week52_low) / week52_low
    nearest_distance = min(distance_from_high, distance_from_low)

    score = _clip((1 - nearest_distance / cfg.WEEK52_PROXIMITY_SATURATION) * 100.0)

    if score > 0:
        which = "52-week high" if distance_from_high <= distance_from_low else "52-week low"
        reasons.append(
            Reason(
                code="WEEK52_PROXIMITY",
                message=f"Price is within {nearest_distance * 100:.2f}% of its {which}.",
                value=nearest_distance,
            )
        )
    return score, reasons, notes


# --------------------------------------------------------------------------- #
# Confidence / data quality
# --------------------------------------------------------------------------- #
def _resolve_confidence(
    data_quality_notes: list[str],
    history_points: Optional[int],
    cfg: type[SignalEngineConfig],
) -> tuple[str, list[str]]:
    notes = list(data_quality_notes)

    critical_missing = any(
        ("current_price" in note or "previous_price" in note or "std dev" in note)
        for note in notes
    )
    insufficient_history = (
        history_points is not None
        and history_points < cfg.MIN_HISTORY_POINTS_FOR_FULL_CONFIDENCE
    )
    if insufficient_history:
        notes.append(
            f"only {history_points} historical observation(s) available "
            f"(fewer than {cfg.MIN_HISTORY_POINTS_FOR_FULL_CONFIDENCE} "
            "needed for full confidence)"
        )

    if critical_missing or insufficient_history:
        return Confidence.LOW.value, notes
    if notes:  # only non-critical gaps remain (e.g. missing benchmark/52w range)
        return Confidence.MEDIUM.value, notes
    return Confidence.HIGH.value, notes


# --------------------------------------------------------------------------- #
# Explanation (deterministic template -- no LLM)
# --------------------------------------------------------------------------- #
def _build_explanation(
    symbol: str,
    attention_score: float,
    direction: str,
    reasons: list[Reason],
    confidence: str,
) -> str:
    if attention_score >= 70:
        level = "high"
    elif attention_score >= 40:
        level = "moderate"
    else:
        level = "low"

    direction_phrase = {
        "UP": "an upward",
        "DOWN": "a downward",
        "NEUTRAL": "no clear directional",
    }[direction]

    sentence = (
        f"{symbol}: {level} attention (score {attention_score:.1f}/100) with "
        f"{direction_phrase} bias."
    )

    contributing = [reason.message for reason in reasons]
    if contributing:
        sentence += " " + " ".join(contributing)

    if confidence != Confidence.HIGH.value:
        sentence += f" (confidence: {confidence})"

    return sentence


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def analyze_attention(
    data: SignalEngineInput,
    config: type[SignalEngineConfig] = SignalEngineConfig,
) -> AttentionAnalysis:
    """
    Score a single instrument's current tick against its baseline
    statistics and return a deterministic attention analysis.

    Never raises on bad/missing input: invalid or absent statistics
    degrade the relevant sub-score to 0 and lower overall confidence
    instead of producing inf/NaN or throwing.
    """
    current_price = _to_float(data.current_price)
    previous_price = _to_float(data.previous_price)
    current_volume = _to_float(data.current_volume)
    avg_return = _to_float(data.avg_return)
    variance = _to_float(data.return_variance)
    std_dev = _to_float(data.return_std_dev)
    avg_volume = _to_float(data.avg_volume)
    benchmark_return = _to_float(data.benchmark_return)
    week52_high = _to_float(data.week52_high)
    week52_low = _to_float(data.week52_low)

    data_quality_notes: list[str] = []

    current_return, price_notes = _compute_current_return(current_price, previous_price)
    data_quality_notes += price_notes

    price_score, price_reasons, price_std_notes = _compute_price_score(
        current_return, avg_return, variance, std_dev, config
    )
    data_quality_notes += price_std_notes

    volume_score, volume_reasons, volume_notes = _compute_volume_score(
        current_volume, avg_volume, config
    )
    data_quality_notes += volume_notes

    relative_score, relative_reasons, relative_notes = _compute_relative_score(
        current_return, benchmark_return, config
    )
    data_quality_notes += relative_notes

    week52_score, week52_reasons, week52_notes = _compute_week52_score(
        current_price, week52_high, week52_low, config
    )
    data_quality_notes += week52_notes

    attention_score = _clip(
        config.WEIGHT_PRICE * price_score
        + config.WEIGHT_VOLUME * volume_score
        + config.WEIGHT_RELATIVE * relative_score
        + config.WEIGHT_WEEK52_PROXIMITY * week52_score
    )

    if current_return is not None and price_score >= config.DIRECTION_PRICE_SCORE_THRESHOLD:
        direction = Direction.UP.value if current_return > 0 else Direction.DOWN.value
    else:
        direction = Direction.NEUTRAL.value

    confidence, data_quality_notes = _resolve_confidence(
        data_quality_notes, data.history_points, config
    )

    all_reasons = price_reasons + volume_reasons + relative_reasons + week52_reasons
    if not all_reasons:
        all_reasons = [
            Reason(
                code="NO_SIGNIFICANT_SIGNAL",
                message="No significant price, volume, or relative anomalies detected.",
            )
        ]

    explanation = _build_explanation(
        data.symbol, attention_score, direction, all_reasons, confidence
    )

    return AttentionAnalysis(
        attention_score=round(attention_score, 2),
        direction=direction,
        price_score=round(price_score, 2),
        volume_score=round(volume_score, 2),
        relative_score=round(relative_score, 2),
        week52_score=round(week52_score, 2),
        confidence=confidence,
        data_quality_notes=data_quality_notes,
        reasons=[reason.__dict__ for reason in all_reasons],
        explanation=explanation,
    )
