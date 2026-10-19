"""Load flight logs into SQLite and run the telemetry quality checks.

    python -m telemetry.quality --csv data/flights.csv --db telemetry.db
"""
from __future__ import annotations

import argparse
import csv
import math
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_PARAMS = {
    "period_ms": 20,          # 50 Hz nominal rate
    "gap_factor": 2.5,        # dt > 2.5 periods = logging gap
    "min_frozen_run": 25,     # 0.5 s of identical readings = stuck sensor
    "max_speed_mps": 40.0,    # faster than the airframe can fly = GPS glitch
    "accel_limit": 16 * 9.80665,  # +-16 g accelerometer full scale
}

NUMERIC = ["t_ms", "ax", "ay", "az", "gx", "gy", "gz", "lat", "lon", "alt_m", "baro_alt_m"]


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres; registered as a SQL function."""
    if None in (lat1, lon1, lat2, lon2):
        return None
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def connect(db: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db))
    conn.create_function("haversine_m", 4, haversine_m, deterministic=True)
    return conn


def _num(v: str):
    v = (v or "").strip()
    if v == "" or v.lower() == "nan":
        return None
    return float(v)


def load_csv(conn: sqlite3.Connection, path: Path) -> int:
    conn.executescript(
        "DROP TABLE IF EXISTS telemetry;"
        "CREATE TABLE telemetry (flight_id TEXT NOT NULL, "
        + ", ".join(f"{c} REAL" for c in NUMERIC) + ");"
    )
    with open(path, newline="") as f:
        rows = [(r["flight_id"],) + tuple(_num(r[c]) for c in NUMERIC) for r in csv.DictReader(f)]
    with conn:
        conn.executemany(f"INSERT INTO telemetry VALUES ({','.join('?' * (len(NUMERIC) + 1))})", rows)
    return len(rows)


def set_params(conn: sqlite3.Connection, **overrides) -> None:
    p = {**DEFAULT_PARAMS, **overrides}
    conn.executescript("DROP TABLE IF EXISTS params;")
    conn.execute("CREATE TABLE params (" + ", ".join(f"{k} REAL" for k in p) + ")")
    conn.execute(f"INSERT INTO params VALUES ({','.join('?' * len(p))})", tuple(p.values()))
    conn.commit()


def run_checks(conn: sqlite3.Connection) -> list[dict]:
    conn.executescript((ROOT / "sql" / "checks.sql").read_text())
    conn.row_factory = sqlite3.Row
    out = [dict(r) for r in conn.execute("SELECT * FROM flight_quality")]
    conn.row_factory = None
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Drone telemetry quality checks")
    ap.add_argument("--csv", default=str(ROOT / "data" / "flights.csv"))
    ap.add_argument("--db", default=str(ROOT / "telemetry.db"))
    a = ap.parse_args(argv)

    conn = connect(a.db)
    n = load_csv(conn, Path(a.csv))
    set_params(conn)
    summary = run_checks(conn)

    cols = ["flight_id", "samples", "completeness_pct", "max_dt_ms", "gaps", "out_of_order",
            "frozen_samples", "gps_jumps", "range_issues", "score", "status"]
    print(f"{n} samples loaded\n")
    print("  ".join(f"{c:>14}" for c in cols))
    for r in summary:
        print("  ".join(f"{str(r[c]):>14}" for c in cols))
    conn.close()
    return 1 if any(r["status"] == "REJECT" for r in summary) else 0


if __name__ == "__main__":
    raise SystemExit(main())
