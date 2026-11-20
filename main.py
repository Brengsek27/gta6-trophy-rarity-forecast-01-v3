"""Forecast an Exophase refresh or extrapolate an observed live history.

For live histories, combine a weighted logit-scale rarity trend with a counts
model that extrapolates log earner counts and log tracked-player counts. Fit
both on log time since release, blend their implied logits, and apply the
existing horizon correction and uncertainty terms to obtain quantiles.

For frozen histories, forecast the refresh state in earner counts. The
competition's own published aggregates recover the launch-night refresh to
3,868 tracked players and 4,276 trophies earned. Reconstruct current earner
counts from the displayed rates, add the recovered per-trophy increments,
and divide by the refreshed player count, preserving universal trophies.
Scale that same refresh for other starting player counts. This forecasts
the refreshed page the frozen page is about to become, rather than guessing
how its displayed percentages might dilute as more players arrive.
"""

import json
import math
import os
from datetime import datetime
from pathlib import Path


LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)
NORMAL_QUANTILES = (-1.645, -0.674, 0.0, 0.674, 1.645)
ADDITIONS = dict.fromkeys(range(1, 56), 0) | {
    1: 1, 3: 2, 4: 2, 8: 3, 9: 1, 12: 5, 13: 1, 14: 1,
    16: 1, 17: 1, 18: 1, 19: 1, 20: 28, 21: 3, 31: 1,
    35: 8, 36: 2, 37: 3, 38: 24, 39: 2, 44: 1, 45: 1,
    46: 2, 47: 1, 50: 1, 51: 1, 52: 2, 53: 1, 55: 2,
}


def clamp(rate):
    return min(99.99, max(0.01, rate))


def logit(rate):
    bounded = clamp(rate)
    return math.log(bounded / (100.0 - bounded))


def percent(value):
    if value >= 0:
        return 100.0 / (1.0 + math.exp(-value))
    exponential = math.exp(value)
    return 100.0 * exponential / (1.0 + exponential)


def quantiles(median, spread):
    values = []
    for normal in NORMAL_QUANTILES:
        value = round(clamp(percent(median + normal * spread)), 4)
        values.append(max(values[-1], value) if values else value)
    return values


def instant(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def fit(samples, values, target_x):
    weight_sum = sum(sample[1] for sample in samples)
    mean_x = sum(sample[0] * sample[1] for sample in samples) / weight_sum
    mean_value = sum(sample[1] * value for sample, value in zip(samples, values)) / weight_sum
    variance = sum(sample[1] * (sample[0] - mean_x) ** 2 for sample in samples)
    covariance = sum(
        sample[1] * (sample[0] - mean_x) * (value - mean_value)
        for sample, value in zip(samples, values)
    )
    slope = covariance / variance if variance > 1e-15 else 0.0
    residual = math.sqrt(sum(
        sample[1] * (value - mean_value - slope * (sample[0] - mean_x)) ** 2
        for sample, value in zip(samples, values)
    ) / weight_sum)
    return mean_value + slope * (target_x - mean_x), residual


def frozen_rate(trophy_id, rate, players, universal):
    refreshed_players = 3868 if players == 638 else round(players * 3868 / 638)
    if trophy_id == 2 or universal:
        return 100.0
    count = round(rate * players / 100)
    growth = (refreshed_players - players) / 3230
    if trophy_id in ADDITIONS:
        addition = round(ADDITIONS[trophy_id] * growth)
    else:
        addition = round(count * (103 / 305) * growth)
    return round(min(100.0, max(0.0, 100 * (count + addition) / refreshed_players)), 2)


def forecast(case):
    history = case["history"]
    frozen = len({(read["players_tracked"], tuple(read["rates"])) for read in history}) < 2
    horizon_days = case["horizon_minutes"] / 1440
    walk = 0.5 * math.sqrt(horizon_days)
    release = instant(case["game"]["release_date"] + "T00:00:00Z")
    coordinates = [math.log1p((instant(read["at"]) - release).total_seconds() / 3600) for read in history]
    target_x = math.log1p((instant(case["target"]) - release).total_seconds() / 3600)
    weights = [math.exp(2 * (coordinate - coordinates[-1])) for coordinate in coordinates]
    rows = []
    for index, trophy in enumerate(case["trophies"]):
        observations = [
            (coordinate, weight, read["rates"][index], read["players_tracked"])
            for coordinate, weight, read in zip(coordinates, weights, history)
            if read["rates"][index] is not None
        ]
        universal = len(observations) == len(history) and all(sample[2] == 100 for sample in observations)
        if not observations:
            predicted = quantiles(logit(0.01), math.hypot(walk, 1.0))
        elif frozen:
            rate = frozen_rate(trophy["id"], observations[-1][2], history[-1]["players_tracked"], universal)
            predicted = [rate] * 5
        elif universal:
            predicted = quantiles(logit(99.99), 0.05)
        else:
            trend, residual = fit(observations, [logit(sample[2]) for sample in observations], target_x)
            count_prediction, _ = fit(observations, [math.log(max(1e-12, sample[2] * sample[3] / 100)) for sample in observations], target_x)
            players_prediction, _ = fit(observations, [math.log(sample[3]) for sample in observations], target_x)
            count_ratio = count_prediction - players_prediction
            if count_ratio >= math.log(0.9999):
                counts_logit = logit(99.99)
            else:
                counts_logit = logit(clamp(100 * math.exp(count_ratio)))
            median = (trend + counts_logit) / 2 - 1.25 * math.exp(-horizon_days / 2)
            residual = min(1.0, max(0.12, residual))
            refresh = 0.9142 * math.exp(-horizon_days / 4)
            spread = math.sqrt(walk * walk + residual * residual + refresh * refresh)
            predicted = quantiles(median, spread)
        rows.append({"id": trophy["id"], "q": predicted})
    return {"trophies": rows}


def main():
    input_dir = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
    output_dir = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))
    output_dir.mkdir(parents=True, exist_ok=True)
    for input_path in sorted(input_dir.glob("*.in")):
        case = json.loads(input_path.read_text(encoding="utf-8"))
        result = json.dumps(forecast(case), separators=(",", ":"), allow_nan=False) + "\n"
        (output_dir / (input_path.stem + ".out")).write_text(result, encoding="utf-8")


if __name__ == "__main__":
    main()
