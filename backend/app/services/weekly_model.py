"""Reproducible walk-forward model selection shared by serving and benchmarking.

Fits residual corrections to the existing recency baseline. Every feature uses
strictly earlier games. The penultimate completed season selects a ridge penalty
and calibrates intervals; the last completed season is untouched until the final
baseline comparison. The exact admitted weights are then used by serving.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
from collections import defaultdict
from functools import lru_cache
from statistics import fmean

VERSION = "weekly-walk-forward-v2"
FEATURES = ("recent_production_delta", "usage_trend_points", "opponent_allowed_delta")
MIN_TRAIN = 300
MIN_HOLDOUT = 300


def history(rows: list[dict], season: int, week: int) -> list[dict]:
    return [r for r in rows if (season - 2, 0) < (r["season"], r["week"]) < (season, week)][-16:]


def baseline(samples: list[dict]) -> tuple[float, float]:
    weights = [0.88 ** (len(samples) - i - 1) for i in range(len(samples))]
    mean = sum(r["points"] * w for r, w in zip(samples, weights, strict=True)) / sum(weights)
    variance = sum(
        w * (r["points"] - mean) ** 2 for r, w in zip(samples, weights, strict=True)
    ) / sum(weights)
    return mean, max(3.0, math.sqrt(variance))


def opponent_index(stats: dict[str, list[dict]]) -> dict:
    totals = defaultdict(float)
    for rows in stats.values():
        for row in rows:
            if row.get("opponent") and row.get("position"):
                totals[(row["season"], row["week"], row["opponent"], row["position"])] += row[
                    "points"
                ]
    result = defaultdict(list)
    for (season, week, opponent, position), points in sorted(totals.items()):
        result[(opponent, position)].append(((season, week), points))
        result[("ALL", position)].append(((season, week), points))
    return dict(result)


def prior_allowed(
    index: dict, opponent: str, position: str, cutoff: tuple
) -> tuple[float | None, int]:
    def prior(key, count):
        rows = index.get(key, [])
        stop = bisect.bisect_left(rows, (cutoff, -math.inf))
        return [
            value for date, value in rows[max(0, stop - count) : stop] if date[0] >= cutoff[0] - 1
        ]

    opponent_values = prior((opponent, position), 8)
    league_values = prior(("ALL", position), 256)
    if len(opponent_values) < 4 or len(league_values) < 32:
        return None, len(opponent_values)
    return fmean(opponent_values) - fmean(league_values), len(opponent_values)


def features(
    samples: list[dict], opponent: str, position: str, cutoff: tuple, index: dict
) -> tuple[list[float], dict]:
    mean, _ = baseline(samples)
    recent = fmean(r["points"] for r in samples[-3:])
    recent_usage = fmean(r["usage"] for r in samples[-3:])
    baseline_usage = fmean(r["usage"] for r in samples[-8:])
    trend = (recent_usage - baseline_usage) * mean / baseline_usage if baseline_usage > 0 else 0.0
    matchup, count = prior_allowed(index, opponent, position, cutoff)
    return [recent - mean, trend, matchup or 0.0], {
        "recent_production_delta": round(recent - mean, 3),
        "usage_trend_points": round(trend, 3) if baseline_usage > 0 else None,
        "opponent_allowed_delta": round(matchup, 3) if matchup is not None else None,
        "opponent_sample_games": count,
    }


def _solve(matrix: list[list[float]], values: list[float]) -> list[float]:
    rows = [list(row) + [value] for row, value in zip(matrix, values, strict=True)]
    for i in range(len(rows)):
        pivot = max(range(i, len(rows)), key=lambda j: abs(rows[j][i]))
        rows[i], rows[pivot] = rows[pivot], rows[i]
        if abs(rows[i][i]) < 1e-10:
            return [0.0] * len(rows)
        scale = rows[i][i]
        rows[i] = [v / scale for v in rows[i]]
        for j in range(len(rows)):
            if j != i:
                factor = rows[j][i]
                rows[j] = [a - factor * b for a, b in zip(rows[j], rows[i], strict=True)]
    return [row[-1] for row in rows]


def _fit(rows: list[dict], penalty: float) -> dict:
    means = [fmean(r["x"][i] for r in rows) for i in range(len(FEATURES))]
    scales = [
        max(1e-6, math.sqrt(fmean((r["x"][i] - means[i]) ** 2 for r in rows)))
        for i in range(len(FEATURES))
    ]
    design = [
        [1.0, *[(v - mean) / scale for v, mean, scale in zip(r["x"], means, scales, strict=True)]]
        for r in rows
    ]
    n = len(FEATURES) + 1
    matrix = [
        [sum(x[i] * x[j] for x in design) + (penalty if i == j and i else 0) for j in range(n)]
        for i in range(n)
    ]
    target = [
        sum(x[i] * (r["actual"] - r["baseline"]) for x, r in zip(design, rows, strict=True))
        for i in range(n)
    ]
    return {
        "means": means,
        "scales": scales,
        "coefficients": _solve(matrix, target),
        "penalty": penalty,
    }


def corrected(mean: float, values: list[float], fit: dict) -> float:
    x = [1.0, *[(v - m) / s for v, m, s in zip(values, fit["means"], fit["scales"], strict=True)]]
    return mean + sum(v * c for v, c in zip(x, fit["coefficients"], strict=True))


def _quantile(values: list[float], level: float = 0.8) -> float:
    values = sorted(values)
    # Finite-sample split-conformal absolute residual quantile.
    return values[min(len(values) - 1, max(0, math.ceil((len(values) + 1) * level) - 1))]


def _metrics(rows: list[dict], fit: dict | None, width: float) -> dict:
    errors = [
        abs(r["actual"] - (corrected(r["baseline"], r["x"], fit) if fit else r["baseline"]))
        for r in rows
    ]
    return {
        "count": len(rows),
        "mae": round(fmean(errors), 6),
        "rmse": round(math.sqrt(fmean(e * e for e in errors)), 6),
        "interval_coverage": round(fmean(e <= width for e in errors), 6),
        "interval_half_width": round(width, 6),
    }


def benchmark(stats: dict[str, list[dict]], target_season: int) -> dict:
    index = opponent_index(stats)
    seasons = sorted(
        {r["season"] for rows in stats.values() for r in rows if r["season"] < target_season}
    )
    result = {
        "version": VERSION,
        "target_season": target_season,
        "selected": "recency_baseline",
        "status": "insufficient_history",
        "features": list(FEATURES),
        "interval_target": 0.8,
        "fit": None,
        "interval_half_width": None,
        "sample_policy": (
            "Observed player appearances with at least four strictly prior games. "
            "DNPs are not inferred as zero; availability remains a separate gate."
        ),
    }
    if len(seasons) < 3:
        return result
    calibration_season, holdout_season = seasons[-2:]
    train, calibration, holdout = [], [], []
    for identity, appearances in sorted(stats.items()):
        appearances = sorted(appearances, key=lambda r: (r["season"], r["week"]))
        for row in appearances:
            if row["season"] >= target_season or row.get("position") not in {
                "QB",
                "RB",
                "WR",
                "TE",
                "K",
                "DEF",
            }:
                continue
            prior = history(appearances, row["season"], row["week"])
            if len(prior) < 4:
                continue
            mean, _ = baseline(prior)
            x, _ = features(
                prior, row.get("opponent", ""), row["position"], (row["season"], row["week"]), index
            )
            record = {
                "identity": identity,
                "season": row["season"],
                "week": row["week"],
                "position": row["position"],
                "baseline": mean,
                "x": x,
                "actual": row["points"],
            }
            (
                holdout
                if row["season"] == holdout_season
                else calibration
                if row["season"] == calibration_season
                else train
            ).append(record)
    result.update(
        {
            "training_seasons": seasons[:-2],
            "calibration_season": calibration_season,
            "holdout_season": holdout_season,
            "training_count": len(train),
            "calibration_count": len(calibration),
            "holdout_count": len(holdout),
        }
    )
    if len(train) < MIN_TRAIN or len(calibration) < MIN_HOLDOUT or len(holdout) < MIN_HOLDOUT:
        return result
    candidates = [_fit(train, penalty) for penalty in (10.0, 100.0, 1000.0)]
    fits = sorted(
        candidates,
        key=lambda fit: fmean(
            abs(r["actual"] - corrected(r["baseline"], r["x"], fit)) for r in calibration
        ),
    )
    fit = fits[0]
    width = _quantile(
        [abs(r["actual"] - corrected(r["baseline"], r["x"], fit)) for r in calibration]
    )
    baseline_width = _quantile([abs(r["actual"] - r["baseline"]) for r in calibration])
    candidate_metrics = _metrics(holdout, fit, width)
    baseline_metrics = _metrics(holdout, None, baseline_width)
    # A candidate must improve the identical held-out sample and retain useful interval coverage.
    admitted = (
        candidate_metrics["mae"] < baseline_metrics["mae"]
        and candidate_metrics["interval_coverage"] >= 0.7
    )
    by_position = {}
    for position in sorted({r["position"] for r in holdout}):
        subset = [r for r in holdout if r["position"] == position]
        candidate = _metrics(subset, fit, width)
        base = _metrics(subset, None, baseline_width)
        by_position[position] = {
            "candidate": candidate,
            "baseline": base,
            "admitted": bool(
                admitted
                and len(subset) >= 100
                and candidate["mae"] < base["mae"]
                and candidate["interval_coverage"] >= 0.7
            ),
        }
    result.update(
        {
            "status": "evaluated",
            "selected": "ridge_usage_matchup" if admitted else "recency_baseline",
            "fit": fit if admitted else None,
            "candidate_fit": fit,
            "interval_half_width": width if admitted else baseline_width,
            "baseline_interval_half_width": baseline_width,
            "candidate": candidate_metrics,
            "baseline": baseline_metrics,
            "positions": by_position,
            "admission_rule": (
                "Lower paired holdout MAE and at least 70% coverage for an 80% "
                "interval; per-position improvement and at least 100 holdout "
                "appearances also required for serving."
            ),
        }
    )
    return result


@lru_cache(maxsize=8)
def _cached_benchmark(serialized: str, target_season: int) -> dict:
    result = benchmark(json.loads(serialized), target_season)
    result["input_sha256"] = hashlib.sha256(serialized.encode()).hexdigest()
    return result


def select_model(stats: dict[str, list[dict]], target_season: int) -> dict:
    # Completed seasons only. Current-week results cannot change model admission.
    historical = {
        key: [r for r in rows if r["season"] < target_season] for key, rows in stats.items()
    }
    return _cached_benchmark(
        json.dumps(historical, sort_keys=True, separators=(",", ":")), target_season
    )


def predict(
    samples: list[dict],
    opponent: str,
    position: str,
    season: int,
    week: int,
    index: dict,
    model: dict,
) -> dict:
    mean, descriptive_width = baseline(samples)
    values, evidence = features(samples, opponent, position, (season, week), index)
    admitted = model.get("positions", {}).get(position, {}).get("admitted", False)
    points = corrected(mean, values, model["fit"]) if admitted else mean
    width = (
        model.get("interval_half_width") if admitted else model.get("baseline_interval_half_width")
    )
    return {
        "points": points,
        "floor": points - (width or descriptive_width),
        "ceiling": points + (width or descriptive_width),
        "method": "ridge_usage_matchup" if admitted else "recency_baseline",
        "interval_kind": "historically_calibrated_80_percent"
        if width
        else "descriptive_historical_spread",
        "features": evidence,
    }
