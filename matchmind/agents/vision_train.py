"""Train the Vision agent's shot classifier on synthetic tracking data.

Each ball flight the Vision agent sees becomes one sample (13 features: speed,
distance and angle to goal, directness, height, who received it...). The label
comes from the simulator's ground truth: was this flight a shot?

Model: L2-regularised logistic regression fitted with Newton's method (IRLS),
in pure Python so inference needs no ML dependencies. Training matches (seeds
101+) never overlap the evaluation matches (seeds 1-20).

  python -m matchmind.cli train-vision            # writes matchmind/agents/vision_model.json
"""

from __future__ import annotations

import json
import math
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from matchmind.agents.vision import FEATURES, MODEL_PATH, VisionAutoEventer
from matchmind.sim.engine import generate_match
from matchmind.sim.tracking import tracking_feed


def collect(seeds: list[int]) -> tuple[list[list[float]], list[int]]:
    xs: list[list[float]] = []
    ys: list[int] = []
    for seed in seeds:
        meta, truth = generate_match(seed)
        flights: list[dict[str, Any]] = []
        vision = VisionAutoEventer(meta, model=None, collect=flights)
        for kind, item in tracking_feed(truth, seed):
            vision.official(item) if kind == "official" else vision.frame(item)
        shots = [(e.player_id, e.t) for e in truth if e.type.value == "shot"]
        for fl in flights:
            xs.append(fl["x"])
            ys.append(int(any(p == fl["player_id"] and abs(t - fl["t0"]) <= 1.0 for p, t in shots)))
    return xs, ys


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (13x13)."""
    n = len(b)
    m = [[*row[:], b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        for r in range(c + 1, n):
            f = m[r][c] / m[c][c]
            for k in range(c, n + 1):
                m[r][k] -= f * m[c][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))) / m[r][r]
    return x


def fit(xs: list[list[float]], ys: list[int], weights: list[float], l2: float = 1e-2, iters: int = 12) -> list[float]:
    n = len(xs[0])
    w = [0.0] * n
    for _ in range(iters):
        g = [0.0] * n
        h = [[0.0] * n for _ in range(n)]
        for x, y, sw in zip(xs, ys, weights, strict=True):
            z = max(-30.0, min(30.0, sum(wi * xi for wi, xi in zip(w, x, strict=True))))
            p = 1 / (1 + math.exp(-z))
            r = sw * (p - y)
            s = sw * p * (1 - p)
            for i in range(n):
                g[i] += r * x[i]
                xi_s = x[i] * s
                hi = h[i]
                for j in range(i, n):
                    hi[j] += xi_s * x[j]
        for i in range(n):
            g[i] += l2 * w[i] if i else 0.0
            h[i][i] += l2
            for j in range(i):
                h[i][j] = h[j][i]
        step = _solve(h, g)
        w = [wi - si for wi, si in zip(w, step, strict=True)]
        if max(abs(s) for s in step) < 1e-6:
            break
    return w


def _prob(w: list[float], x: list[float]) -> float:
    z = max(-30.0, min(30.0, sum(a * b for a, b in zip(w, x, strict=True))))
    return 1 / (1 + math.exp(-z))


def train(train_seeds: list[int], val_seeds: list[int], neg_keep: float = 0.25, out: Path = MODEL_PATH) -> dict[str, Any]:
    xs, ys = collect(train_seeds)
    rng = random.Random(0)
    keep = [i for i, y in enumerate(ys) if y or rng.random() < neg_keep]  # down-sample easy negatives
    w = fit([xs[i] for i in keep], [ys[i] for i in keep], [1.0 if ys[i] else 1 / neg_keep for i in keep])

    vx, vy = collect(val_seeds)
    probs = [_prob(w, x) for x in vx]
    best = (0.0, 0.5)
    for th in [i / 100 for i in range(10, 91, 2)]:
        tp = sum(p >= th and y for p, y in zip(probs, vy, strict=True))
        fp = sum(p >= th and not y for p, y in zip(probs, vy, strict=True))
        fn = sum(p < th and y for p, y in zip(probs, vy, strict=True))
        f1 = 2 * tp / max(1, 2 * tp + fp + fn)
        best = max(best, (f1, th))
    f1, th = best
    tp = sum(p >= th and y for p, y in zip(probs, vy, strict=True))
    model = {
        "name": "matchmind-vision-shot-classifier",
        "type": "logistic_regression",
        "features": FEATURES,
        "weights": [round(v, 6) for v in w],
        "threshold": th,
        "trained_on": {"seeds": f"{train_seeds[0]}-{train_seeds[-1]}", "flights": len(xs), "shots": sum(ys)},
        "validation": {"seeds": f"{val_seeds[0]}-{val_seeds[-1]}", "flights": len(vx), "shots": sum(vy), "f1": round(f1, 3),
                       "recall": round(tp / max(1, sum(vy)), 3),
                       "precision": round(tp / max(1, sum(p >= th for p in probs)), 3)},
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    out.write_text(json.dumps(model, indent=2) + "\n")
    return model
