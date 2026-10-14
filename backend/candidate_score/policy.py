"""打分口径：默认权重与策略、权重归一化，以及三个内部小工具。"""

from __future__ import annotations

import math
import re

DEFAULT_WEIGHTS = {
    "quality": 0.34,
    "speed": 0.28,
    "experience": 0.26,
    "fit": 0.12,
}

# Inner weights: how each dimension blends its own signals.
QUALITY_INNER_WEIGHTS = {
    "resolution": 0.45,
    "bitrate": 0.33,
    "source": 0.17,
    "codec": 0.05,
}
EXPERIENCE_INNER_WEIGHTS = {
    "subtitle": 0.42,
    "hygiene": 0.22,
    "container": 0.14,
    "release_group": 0.12,
    "audio": 0.10,
}

DEFAULT_POLICY = {
    "kind": "movie",
    "duration_seconds": 7200.0,
    "target_bitrate_mbps": 5.0,
    "target_size_gb": 7.5,
    "preferred_min_gb": 5.0,
    "preferred_max_gb": 10.0,
    "hard_max_gb": 15.0,
    "speed_reference_mbps": 15.0,
    "seeds_reference": 120.0,
    "peers_reference": 200.0,
    "weights": dict(DEFAULT_WEIGHTS),
    "wanted_markers": [],
}

# Below this score the pool is reported as too weak instead of silently
# submitting the best of a bad set. It is a convenience default; callers can
# lower or raise it with --min-score.
DEFAULT_MIN_SCORE = 45.0


def normalize_policy(policy=None):
    """Fill defaults and normalize the weight vector to sum to 1."""
    merged = dict(DEFAULT_POLICY)
    merged["weights"] = dict(DEFAULT_WEIGHTS)
    for key, value in (policy or {}).items():
        if value is None:
            continue
        if key == "weights" and isinstance(value, dict):
            merged["weights"].update(
                {name: float(item) for name, item in value.items()}
            )
        else:
            merged[key] = value
    total = sum(
        max(float(merged["weights"].get(name, 0.0)), 0.0) for name in DEFAULT_WEIGHTS
    )
    if total <= 0:
        merged["weights"] = dict(DEFAULT_WEIGHTS)
    else:
        merged["weights"] = {
            name: max(float(merged["weights"].get(name, 0.0)), 0.0) / total
            for name in DEFAULT_WEIGHTS
        }
    return merged


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def _log_score(value, reference):
    """Saturating 0-1 curve for counts and speeds."""
    if value is None or value <= 0:
        return 0.0
    if reference <= 0:
        return 0.0
    return _clamp(math.log1p(value) / math.log1p(reference))


def _matches(patterns, text):
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)
