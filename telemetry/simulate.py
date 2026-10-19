"""Generate synthetic 50 Hz drone flight logs (IMU + GPS + barometer) with
known sensor / logging defects injected.

    python -m telemetry.simulate --out data/flights.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from pathlib import Path

PERIOD_MS = 20                     # 50 Hz IMU / log rate
GPS_EVERY = 10                     # GPS fix every 10 samples = 5 Hz (held in between)
G = 9.80665
LAT0, LON0 = 49.0069, 8.4037       # take-off point
FIELDS = ["flight_id", "t_ms", "ax", "ay", "az", "gx", "gy", "gz",
          "lat", "lon", "alt_m", "baro_alt_m"]

# flight -> defect injected (F01 is clean)
DEFECT_PLAN = {
    "F01": None,
    "F02": "dropout",          # 1 s logging gap + three short gaps
    "F03": "out_of_order",     # two swapped records
    "F04": "frozen_imu",       # az stuck for 3 s
    "F05": "gps_jump",         # single GPS fix ~550 m off
    "F06": "accel_saturation", # 3 samples beyond +-16 g
}


def fly(flight_id: str, duration_s: float, rng: random.Random) -> list[dict]:
    """Circle at 8 m/s, radius 100 m, climbing to 30 m."""
    n = int(duration_s * 1000 / PERIOD_MS)
    radius, speed = 100.0, 8.0
    rows = []
    fix = (LAT0, LON0)
    for i in range(n):
        t = i * PERIOD_MS / 1000
        theta = speed * t / radius
        north, east = radius * math.sin(theta), radius * (1 - math.cos(theta))
        alt = min(30.0, 3.0 * t)
        if i % GPS_EVERY == 0:  # new GPS fix; otherwise the logger repeats the last one
            fix = (round(LAT0 + north / 111_320 + rng.gauss(0, 2e-6), 7),
                   round(LON0 + east / (111_320 * math.cos(math.radians(LAT0))) + rng.gauss(0, 2e-6), 7))
        rows.append({
            "flight_id": flight_id,
            "t_ms": i * PERIOD_MS,
            "ax": round(rng.gauss(0, 0.05), 4),
            "ay": round(speed ** 2 / radius + rng.gauss(0, 0.05), 4),
            "az": round(-G + rng.gauss(0, 0.05), 4),
            "gx": round(rng.gauss(0, 0.002), 5),
            "gy": round(rng.gauss(0, 0.002), 5),
            "gz": round(speed / radius + rng.gauss(0, 0.002), 5),
            "lat": fix[0],
            "lon": fix[1],
            "alt_m": round(alt + rng.gauss(0, 0.3), 2),
            "baro_alt_m": round(alt + rng.gauss(0, 0.5), 2),
        })
    return rows


def inject(rows: list[dict], defect: str | None, rng: random.Random) -> tuple[list[dict], dict]:
    n = len(rows)
    info: dict = {"defect": defect}
    if defect == "dropout":
        start = n // 2
        del rows[start:start + 50]                         # 1 s gap
        for k in (n // 5, n // 4, (3 * n) // 4):
            del rows[k:k + 3]                              # 80 ms gaps
        info.update(gaps=4, missing_samples=59)
    elif defect == "out_of_order":
        for k in (n // 3, (2 * n) // 3):
            k = k - k % GPS_EVERY + 4          # inside a GPS block, so the swap is timing-only
            rows[k], rows[k + 1] = rows[k + 1], rows[k]
        info.update(out_of_order=2)
    elif defect == "frozen_imu":
        start = n // 2
        for r in rows[start:start + 150]:
            r["az"] = rows[start]["az"]
        info.update(frozen_samples=150)
    elif defect == "gps_jump":
        k = (n // 2) - (n // 2) % GPS_EVERY
        for r in rows[k:k + GPS_EVERY]:                    # one bad fix, ~556 m north
            r["lat"] = round(r["lat"] + 0.005, 7)
        info.update(gps_jump_segments=2)                   # jump out + jump back
    elif defect == "accel_saturation":
        for k in rng.sample(range(n), 3):
            rows[k]["az"] = -200.0
        info.update(range_violations=3)
    return rows, info


def generate(out: Path, duration_s: float = 120, seed: int = 1) -> dict:
    rng = random.Random(seed)
    all_rows, manifest = [], {}
    for fid, defect in DEFECT_PLAN.items():
        rows, info = inject(fly(fid, duration_s, rng), defect, rng)
        all_rows += rows
        manifest[fid] = info
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(all_rows)
    out.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/flights.csv")
    ap.add_argument("--duration", type=float, default=120)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    generate(Path(a.out), a.duration, a.seed)
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
