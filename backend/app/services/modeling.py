from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Any

from ..config import settings


def _clamp(value: float, low: float = 0.01, high: float = 0.99) -> float:
    return min(high, max(low, value))


def _logit(probability: float) -> float:
    probability = _clamp(probability)
    return math.log(probability / (1 - probability))


def team_win_probability(
    home_elo: float,
    away_elo: float,
    home_epa: float = 0,
    away_epa: float = 0,
    home_rest: int = 7,
    away_rest: int = 7,
    home_availability: float = 1,
    away_availability: float = 1,
    neutral_site: bool = False,
    market_probability: float | None = None,
    market_weight: float = 0.65,
) -> dict[str, float | str | bool]:
    home_field = 0 if neutral_site else 55
    elo_probability = 1 / (1 + 10 ** (-(home_elo - away_elo + home_field) / 400))
    adjustment = (
        1.5 * (home_epa - away_epa)
        + 0.018 * (home_rest - away_rest)
        + 0.8 * (home_availability - away_availability)
    )
    model_probability = _clamp(1 / (1 + math.exp(-(_logit(elo_probability) + adjustment))))
    if market_probability is None:
        return {
            "home_win_probability": round(model_probability, 4),
            "model_probability": round(model_probability, 4),
            "market_available": False,
            "confidence": "lower",
        }
    weight = _clamp(market_weight, 0, 1)
    blended = _clamp(weight * market_probability + (1 - weight) * model_probability)
    return {
        "home_win_probability": round(blended, 4),
        "model_probability": round(model_probability, 4),
        "market_probability": round(market_probability, 4),
        "market_available": True,
        "confidence": "standard",
    }


def brier_score(probabilities: list[float], outcomes: list[int]) -> float:
    if not probabilities or len(probabilities) != len(outcomes):
        raise ValueError("Probabilities and outcomes must be non-empty and have equal length")
    return round(
        fmean(
            (probability - outcome) ** 2
            for probability, outcome in zip(probabilities, outcomes, strict=True)
        ),
        6,
    )


def train_projection_candidate(
    records: list[dict[str, Any]],
    feature_names: list[str],
    target_name: str = "fantasy_points",
) -> dict[str, Any]:
    if len(records) < 30:
        raise ValueError("At least 30 time-ordered records are required")
    seasons = sorted({int(record["season"]) for record in records})
    if len(seasons) < 2:
        raise ValueError("At least two seasons are required for a holdout")
    holdout_season = seasons[-1]
    training_seasons = seasons[max(0, len(seasons) - 6) : -1]
    training = [record for record in records if int(record["season"]) in training_seasons]
    holdout = [record for record in records if int(record["season"]) == holdout_season]
    if not training or not holdout:
        raise ValueError("Training and holdout rows are both required")

    try:
        import numpy as np
        from sklearn.linear_model import RidgeCV
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:
        raise RuntimeError(
            "Install the 'ml' optional dependencies to train projection models"
        ) from exc

    x_train = np.asarray([[float(row.get(name, 0)) for name in feature_names] for row in training])
    y_train = np.asarray([float(row[target_name]) for row in training])
    x_holdout = np.asarray([[float(row.get(name, 0)) for name in feature_names] for row in holdout])
    y_holdout = np.asarray([float(row[target_name]) for row in holdout])
    pipeline = make_pipeline(StandardScaler(), RidgeCV(alphas=(0.1, 1.0, 10.0, 50.0)))
    pipeline.fit(x_train, y_train)
    learned = pipeline.predict(x_holdout)

    position_means: dict[str, float] = {}
    position_values: dict[str, list[float]] = defaultdict(list)
    for row in training:
        position_values[str(row.get("position") or "ALL")].append(float(row[target_name]))
    overall_mean = float(y_train.mean())
    for position, values in position_values.items():
        position_means[position] = fmean(values)
    baseline = np.asarray(
        [position_means.get(str(row.get("position") or "ALL"), overall_mean) for row in holdout]
    )

    by_position: dict[str, dict[str, float | str]] = {}
    for position in sorted({str(row.get("position") or "ALL") for row in holdout}):
        indices = [
            index
            for index, row in enumerate(holdout)
            if str(row.get("position") or "ALL") == position
        ]
        ridge_mae = float(np.mean(np.abs(learned[indices] - y_holdout[indices])))
        baseline_mae = float(np.mean(np.abs(baseline[indices] - y_holdout[indices])))
        by_position[position] = {
            "ridge_mae": round(ridge_mae, 4),
            "baseline_mae": round(baseline_mae, 4),
            "selected": "ridge" if ridge_mae < baseline_mae else "baseline",
        }

    scaler = pipeline.named_steps["standardscaler"]
    ridge = pipeline.named_steps["ridgecv"]
    artifact = {
        "model_version": f"ridge-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
        "trained_at": datetime.now(UTC).isoformat(),
        "training_seasons": training_seasons,
        "holdout_season": holdout_season,
        "features": feature_names,
        "target": target_name,
        "mean": scaler.mean_.tolist(),
        "scale": scaler.scale_.tolist(),
        "coefficients": ridge.coef_.tolist(),
        "intercept": float(ridge.intercept_),
        "alpha": float(ridge.alpha_),
        "baseline_position_means": position_means,
        "backtest": {
            "ridge_mae": round(float(np.mean(np.abs(learned - y_holdout))), 4),
            "baseline_mae": round(float(np.mean(np.abs(baseline - y_holdout))), 4),
            "by_position": by_position,
        },
    }
    target = settings.data_dir / "models" / f"{artifact['model_version']}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    artifact["artifact"] = target.name
    return artifact


def list_model_artifacts() -> list[Path]:
    return sorted((settings.data_dir / "models").glob("ridge-*.json"), reverse=True)
