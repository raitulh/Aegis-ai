"""Deterministic synthetic dataset generators for the demo seed.

Every dataset here is fabricated from simple formulas with a fixed random seed — none of it describes
real people, places or measurements. Each generator returns (train_csv, test_csv, sample_submission_csv,
ground_truth_csv, predict) where ``predict(skill, rng)`` produces a submission CSV whose quality grows
with ``skill`` in [0, 1] — used to create plausible, *really scored* demo leaderboards.
"""

from __future__ import annotations

import csv
import io
import math
import random
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class GeneratedTask:
    train: str
    test: str
    sample: str
    ground_truth: str
    predict: Callable[[float, random.Random], str]
    readme: str


def _csv(header: list[str], rows: list[list[object]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def _split_usage(n: int, rng: random.Random, public_share: float = 0.4) -> list[str]:
    return ["Public" if rng.random() < public_share else "Private" for _ in range(n)]


def crop_yield(seed: int = 7, n_train: int = 600, n_test: int = 400) -> GeneratedTask:
    rng = random.Random(seed)
    regions = ["north", "south", "east", "west", "central"]
    region_effect = {"north": -0.4, "south": 0.3, "east": 0.1, "west": -0.1, "central": 0.2}

    def sample_row() -> tuple[list[object], float]:
        rain = rng.uniform(300, 1200)
        temp = rng.uniform(14, 34)
        ph = rng.uniform(5.2, 7.8)
        fert = rng.uniform(0, 220)
        region = rng.choice(regions)
        y = (2.0 + 0.004 * rain - 0.08 * (temp - 24) ** 2 / 4 - 0.6 * (ph - 6.5) ** 2 + 0.012 * fert
             - 0.00003 * fert ** 2 + region_effect[region] + rng.gauss(0, 0.35))
        return [round(rain, 1), round(temp, 2), round(ph, 2), round(fert, 1), region], round(max(0.2, y), 3)

    header = ["id", "rainfall_mm", "avg_temp_c", "soil_ph", "fertilizer_kg_ha", "region"]
    train_rows, test_rows, truth = [], [], []
    for i in range(n_train):
        feats, y = sample_row()
        train_rows.append([f"tr{i:05d}", *feats, y])
    usage = _split_usage(n_test, rng)
    for i in range(n_test):
        feats, y = sample_row()
        test_rows.append([f"te{i:05d}", *feats])
        truth.append((f"te{i:05d}", y, usage[i]))

    def predict(skill: float, r: random.Random) -> str:
        noise = 1.4 * (1 - skill) + 0.25
        return _csv(["id", "yield_t_ha"], [[tid, round(max(0.0, y + r.gauss(0, noise)), 3)] for tid, y, _ in truth])

    return GeneratedTask(
        train=_csv(header + ["yield_t_ha"], train_rows), test=_csv(header, test_rows),
        sample=_csv(["id", "yield_t_ha"], [[tid, 3.0] for tid, _, _ in truth]),
        ground_truth=_csv(["id", "yield_t_ha", "Usage"], [[tid, y, u] for tid, y, u in truth]),
        predict=predict,
        readme="Synthetic crop-yield data generated from a fixed formula plus noise. Not real agricultural measurements.",
    )


def energy_anomaly(seed: int = 11, n_train: int = 800, n_test: int = 500) -> GeneratedTask:
    rng = random.Random(seed)
    buildings = ["library", "lab-a", "lab-b", "dorm-1", "dorm-2", "gym", "admin"]

    def sample_row() -> tuple[list[object], int, float]:
        hour = rng.randint(0, 23)
        base = 40 + 25 * math.sin((hour - 6) / 24 * 2 * math.pi) + rng.gauss(0, 4)
        temp = rng.uniform(-5, 35)
        occupancy = max(0.0, min(1.0, rng.gauss(0.5 if 8 <= hour <= 20 else 0.1, 0.15)))
        anomaly = rng.random() < 0.12
        kwh = base + 0.9 * abs(temp - 20) + 30 * occupancy + (rng.uniform(25, 60) if anomaly else 0)
        logit = -2.2 + (3.4 if anomaly else 0) + rng.gauss(0, 0.9)
        return [rng.choice(buildings), hour, round(temp, 1), round(occupancy, 3), round(kwh, 2)], int(anomaly), logit

    header = ["id", "building", "hour", "outdoor_temp_c", "occupancy_rate", "kwh"]
    train_rows, test_rows, truth = [], [], []
    for i in range(n_train):
        feats, label, _ = sample_row()
        train_rows.append([f"m{i:05d}", *feats, label])
    usage = _split_usage(n_test, rng)
    for i in range(n_test):
        feats, label, logit = sample_row()
        test_rows.append([f"x{i:05d}", *feats])
        truth.append((f"x{i:05d}", label, usage[i], logit))

    def predict(skill: float, r: random.Random) -> str:
        rows = []
        for tid, _label, _, logit in truth:
            signal = logit * skill + r.gauss(0, 1.6 * (1 - skill) + 0.2)
            rows.append([tid, round(1 / (1 + math.exp(-signal)), 5)])
        return _csv(["id", "is_anomaly"], rows)

    return GeneratedTask(
        train=_csv(header + ["is_anomaly"], train_rows), test=_csv(header, test_rows),
        sample=_csv(["id", "is_anomaly"], [[tid, 0.5] for tid, *_ in truth]),
        ground_truth=_csv(["id", "is_anomaly", "Usage"], [[tid, label, u] for tid, label, u, _ in truth]),
        predict=predict,
        readme="Synthetic hourly building-energy readings with injected anomalies. Buildings and readings are fictional.",
    )


_POS = ["clear explanations", "great pacing", "useful labs", "helpful office hours", "fair grading", "engaging projects"]
_NEG = ["confusing slides", "rushed lectures", "unclear rubric", "outdated examples", "too much busywork", "late feedback"]
_NEU = ["standard workload", "average difficulty", "typical format", "regular quizzes", "normal pace", "mixed topics"]


def course_reviews(seed: int = 23, n_train: int = 700, n_test: int = 450) -> GeneratedTask:
    rng = random.Random(seed)
    labels = ["positive", "neutral", "negative"]

    def sample_row() -> tuple[str, str]:
        label = rng.choices(labels, weights=[0.45, 0.25, 0.30])[0]
        pool = {"positive": _POS, "neutral": _NEU, "negative": _NEG}[label]
        other = rng.choice([_POS, _NEU, _NEG])
        parts = [rng.choice(pool), rng.choice(pool), rng.choice(other)]
        rng.shuffle(parts)
        return f"The course had {parts[0]}, {parts[1]} and {parts[2]}.", label

    train_rows = [[f"r{i:05d}", *sample_row()] for i in range(n_train)]
    usage = _split_usage(n_test, rng)
    truth = []
    test_rows = []
    for i in range(n_test):
        text, label = sample_row()
        test_rows.append([f"q{i:05d}", text])
        truth.append((f"q{i:05d}", label, usage[i]))

    def predict(skill: float, r: random.Random) -> str:
        return _csv(["id", "sentiment"], [[tid, label if r.random() < 0.35 + 0.6 * skill else r.choice(labels)] for tid, label, _ in truth])

    return GeneratedTask(
        train=_csv(["id", "review", "sentiment"], train_rows), test=_csv(["id", "review"], test_rows),
        sample=_csv(["id", "sentiment"], [[tid, "neutral"] for tid, _, _ in truth]),
        ground_truth=_csv(["id", "sentiment", "Usage"], [[tid, label, u] for tid, label, u in truth]),
        predict=predict,
        readme="Template-generated course reviews with three sentiment classes. No real student feedback is included.",
    )


def survival_practice(seed: int = 3, n_train: int = 500, n_test: int = 300) -> GeneratedTask:
    rng = random.Random(seed)

    def sample_row() -> tuple[list[object], int]:
        klass = rng.choice([1, 2, 3])
        age = max(1, int(rng.gauss(30, 13)))
        fare = round(max(5.0, rng.gauss({1: 80, 2: 25, 3: 12}[klass], 8)), 2)
        cabin_deck = rng.choice(["A", "B", "C", "D", "E", "none"])
        p = 0.25 + (0.3 if klass == 1 else 0.1 if klass == 2 else 0) + (0.15 if age < 14 else 0) + rng.gauss(0, 0.12)
        return [klass, age, fare, cabin_deck], int(rng.random() < max(0.02, min(0.95, p)))

    header = ["id", "ticket_class", "age", "fare", "cabin_deck"]
    train_rows, test_rows, truth = [], [], []
    for i in range(n_train):
        feats, y = sample_row()
        train_rows.append([f"p{i:04d}", *feats, y])
    usage = _split_usage(n_test, rng, 0.5)
    for i in range(n_test):
        feats, y = sample_row()
        test_rows.append([f"t{i:04d}", *feats])
        truth.append((f"t{i:04d}", y, usage[i]))

    def predict(skill: float, r: random.Random) -> str:
        return _csv(["id", "survived"], [[tid, y if r.random() < 0.55 + 0.35 * skill else 1 - y] for tid, y, _ in truth])

    return GeneratedTask(
        train=_csv(header + ["survived"], train_rows), test=_csv(header, test_rows),
        sample=_csv(["id", "survived"], [[tid, 0] for tid, _, _ in truth]),
        ground_truth=_csv(["id", "survived", "Usage"], [[tid, y, u] for tid, y, u in truth]),
        predict=predict,
        readme="A beginner-friendly synthetic passenger-survival dataset for practising binary classification.",
    )


def simple_table(kind: str, seed: int, n: int = 400) -> str:
    """Stand-alone demo datasets (no competition attached)."""
    rng = random.Random(seed)
    if kind == "mobility":
        stops = ["Library", "Engineering", "Dorms", "Stadium", "Main Gate", "Science Park"]
        rows = [[f"trip{i:05d}", rng.choice(stops), rng.choice(stops), rng.randint(6, 23), round(rng.uniform(0.3, 6.5), 2),
                 rng.choice(["bike", "shuttle", "walk", "scooter"])] for i in range(n)]
        return _csv(["trip_id", "origin", "destination", "hour", "distance_km", "mode"], rows)
    genres = ["fiction", "science", "history", "engineering", "art", "economics"]
    rows = [[f"loan{i:05d}", rng.choice(genres), rng.randint(1, 60), rng.choice(["undergrad", "graduate", "staff"]),
             rng.choice([0, 0, 0, 1])] for i in range(n)]
    return _csv(["loan_id", "genre", "days_borrowed", "borrower_type", "returned_late"], rows)
