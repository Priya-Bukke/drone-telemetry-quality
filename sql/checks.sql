-- Telemetry quality checks, all expressed as SQL window functions.
-- Rows are evaluated in LOG (arrival) order = telemetry.rowid, which is what
-- reveals out-of-order timestamps. Parameters live in the `params` table.

DROP VIEW IF EXISTS v_steps;
CREATE VIEW v_steps AS
SELECT
    t.rowid                                                     AS rid,
    t.*,
    t.t_ms - LAG(t.t_ms) OVER w                                 AS dt_ms,
    CASE WHEN t.lat IS NOT LAG(t.lat) OVER w
           OR t.lon IS NOT LAG(t.lon) OVER w THEN 1 ELSE 0 END  AS new_fix
FROM telemetry t
WINDOW w AS (PARTITION BY t.flight_id ORDER BY t.rowid);


-- 1. Timing: logging gaps and timestamps that go backwards
DROP VIEW IF EXISTS v_timing_issues;
CREATE VIEW v_timing_issues AS
SELECT flight_id, rid, t_ms, dt_ms,
       CASE WHEN dt_ms <= 0 THEN 'out_of_order' ELSE 'gap' END AS issue
FROM v_steps, params
WHERE dt_ms <= 0 OR dt_ms > params.gap_factor * params.period_ms;


-- 2. Frozen sensor: same value repeated for >= min_frozen_run samples
--    (gaps-and-islands: a new island starts whenever the value changes)
DROP VIEW IF EXISTS v_frozen_runs;
CREATE VIEW v_frozen_runs AS
WITH ch AS (
    SELECT flight_id, rid, 'az' AS channel, az AS val,
           CASE WHEN az = LAG(az) OVER (PARTITION BY flight_id ORDER BY rid) THEN 0 ELSE 1 END AS new_run
    FROM v_steps
    UNION ALL
    SELECT flight_id, rid, 'gz', gz,
           CASE WHEN gz = LAG(gz) OVER (PARTITION BY flight_id ORDER BY rid) THEN 0 ELSE 1 END
    FROM v_steps
),
islands AS (
    SELECT *, SUM(new_run) OVER (PARTITION BY flight_id, channel ORDER BY rid
                                 ROWS UNBOUNDED PRECEDING) AS run_no
    FROM ch
)
SELECT flight_id, channel, run_no, MIN(rid) AS first_rid, COUNT(*) AS n_samples, MIN(val) AS stuck_value
FROM islands, params
GROUP BY flight_id, channel, run_no
HAVING COUNT(*) >= MAX(params.min_frozen_run);


-- 3. GPS jumps: implied ground speed between consecutive GPS fixes is impossible.
--    GPS arrives at ~5 Hz but is logged at 50 Hz (last fix repeated), so speed
--    must be computed between NEW fixes, not between log rows.
DROP VIEW IF EXISTS v_gps_jumps;
CREATE VIEW v_gps_jumps AS
WITH fixes AS (
    SELECT flight_id, rid, t_ms,
           t_ms - LAG(t_ms) OVER w                                      AS fix_dt_ms,
           haversine_m(LAG(lat) OVER w, LAG(lon) OVER w, lat, lon)      AS step_m
    FROM v_steps
    WHERE new_fix = 1
    WINDOW w AS (PARTITION BY flight_id ORDER BY rid)
)
SELECT flight_id, rid, t_ms, ROUND(step_m, 1) AS step_m,
       ROUND(step_m / (fix_dt_ms / 1000.0), 1) AS speed_mps
FROM fixes, params
WHERE fix_dt_ms > 0 AND step_m / (fix_dt_ms / 1000.0) > params.max_speed_mps;


-- 4. Physical range: accelerometer beyond sensor full scale, missing altitude
DROP VIEW IF EXISTS v_range_issues;
CREATE VIEW v_range_issues AS
SELECT flight_id, rid, t_ms,
       CASE WHEN alt_m IS NULL THEN 'alt_missing' ELSE 'accel_out_of_range' END AS issue
FROM v_steps, params
WHERE ABS(ax) > params.accel_limit OR ABS(ay) > params.accel_limit
   OR ABS(az) > params.accel_limit OR alt_m IS NULL;


-- Per-flight summary + quality score
DROP TABLE IF EXISTS flight_quality;
CREATE TABLE flight_quality AS
WITH base AS (
    SELECT flight_id,
           COUNT(*)                                        AS samples,
           (MAX(t_ms) - MIN(t_ms)) / (SELECT period_ms FROM params) + 1 AS expected_samples,
           MAX(CASE WHEN dt_ms > 0 THEN dt_ms END)         AS max_dt_ms
    FROM v_steps GROUP BY flight_id
),
agg AS (
    SELECT b.*,
        (SELECT COUNT(*) FROM v_timing_issues x WHERE x.flight_id = b.flight_id AND issue = 'gap')          AS gaps,
        (SELECT COUNT(*) FROM v_timing_issues x WHERE x.flight_id = b.flight_id AND issue = 'out_of_order') AS out_of_order,
        (SELECT COALESCE(SUM(n_samples), 0) FROM v_frozen_runs x WHERE x.flight_id = b.flight_id)           AS frozen_samples,
        (SELECT COUNT(*) FROM v_gps_jumps x WHERE x.flight_id = b.flight_id)                                 AS gps_jumps,
        (SELECT COUNT(*) FROM v_range_issues x WHERE x.flight_id = b.flight_id)                              AS range_issues
    FROM base b
),
scored AS (
    SELECT *,
        ROUND(100.0 * samples / expected_samples, 2) AS completeness_pct,
        MAX(0, MIN(100, ROUND(
              100.0 * samples / expected_samples
            - 5  * out_of_order
            - 10 * gps_jumps
            - 0.1 * frozen_samples
            - 2  * range_issues, 1))) AS score
    FROM agg
)
SELECT *,
    CASE
        WHEN score < 70 THEN 'REJECT'
        WHEN score < 99 OR max_dt_ms > 500 THEN 'REVIEW'
        ELSE 'OK'
    END AS status
FROM scored
ORDER BY flight_id;
