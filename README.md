# Drone Telemetry Quality Checks

Automated data-quality checks for high-rate drone flight logs (50 Hz IMU,
5 Hz GPS, barometer), written almost entirely in **SQL window functions**,
with a per-flight quality score.

Motivation: navigation-filter development is only as good as the logs it is
tuned on. A dropped second of IMU data, a stuck accelerometer or a single bad
GPS fix can silently ruin an analysis, so every log is scored before use.

## Quick start

```bash
python -m telemetry.simulate --out data/flights.csv    # 6 flights, 5 with known defects
python -m telemetry.quality  --csv data/flights.csv    # load -> checks -> per-flight score
python -m pytest -q                                    # 8 tests
```

No dependencies beyond the Python standard library.

Example output:

```
flight  samples  completeness  max_dt  gaps  out_of_order  frozen  gps_jumps  range  score  status
F01        6000        100.0      20     0             0       0          0      0  100.0  OK
F02        5941        99.02    1020     4             0       0          0      0   99.0  REVIEW
F03        6000        100.0      40     0             2       0          0      0   90.0  REVIEW
F04        6000        100.0      20     0             0     150          0      0   85.0  REVIEW
F05        6000        100.0      20     0             0       0          2      0   80.0  REVIEW
F06        6000        100.0      20     0             0       0          0      3   94.0  REVIEW
```

## Checks (`sql/checks.sql`)

| Check | SQL technique | Catches |
|---|---|---|
| Timing | `LAG(t_ms)` in log order | logging dropouts (dt > 2.5 periods), timestamps going backwards |
| Frozen sensor | gaps-and-islands (`LAG` + running `SUM`) | IMU channel repeating the identical value ≥ 0.5 s |
| GPS jump | `LAG` over **new fixes only** + haversine UDF | implied ground speed > 40 m/s |
| Physical range | simple predicates | accelerometer beyond ±16 g full scale, missing altitude |
| Score | aggregation | completeness minus weighted penalties → OK / REVIEW / REJECT |

All thresholds live in a `params` table, so they can change without touching SQL.

## Things I learned building it

- **Order matters.** Checks run in log (arrival) order, not timestamp order — sorting by time would hide out-of-order records.
- **Sensor rates differ from log rates.** GPS updates at ~5 Hz but is logged at 50 Hz with the last fix repeated. Computing speed between log rows produced constant false "jumps" from normal GPS noise; computing it between *new* fixes removed them all (verified by a test).
- **Swaps ≠ gaps.** Two swapped records create one negative and one double-length step; the gap threshold (2.5× period) is set so a swap is reported once, as out-of-order only.
- SQLite has no trigonometry by default, so the haversine distance is a Python function registered with `create_function`.
