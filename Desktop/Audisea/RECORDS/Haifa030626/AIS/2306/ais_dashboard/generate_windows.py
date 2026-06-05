#!/usr/bin/env python3
"""
Generate labeling windows from AIS data for NN training.

Usage:
    python generate_windows.py [--from "2026-06-03 11:00:00 UTC"] [--to "2026-06-03 19:00:00 UTC"]
                               [--window 30] [--range 10]

Defaults to 2026-06-03 11:00–19:00 UTC, 30-second windows, 10 km audible range.
Writes ais_dashboard/labels.db.
"""
import os, sys, glob, sqlite3, math, argparse
from datetime import datetime, timedelta

HYDROPHONE_LAT = 32.843153
HYDROPHONE_LON = 34.971938

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_parent  = os.path.join(BASE_DIR, '..')
DATA_DIR = os.path.join(_parent, 'ais_data') if os.path.isdir(os.path.join(_parent, 'ais_data')) else _parent
OUTPUT   = os.path.join(BASE_DIR, 'labels.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS label_windows (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    t_start               TEXT NOT NULL UNIQUE,
    t_end                 TEXT NOT NULL,
    duration_sec          INTEGER NOT NULL,

    -- AIS auto-context (nearest vessel within range at window midpoint ±2 min)
    ais_vessel_count      INTEGER DEFAULT 0,
    ais_closest_mmsi      TEXT,
    ais_closest_name      TEXT,
    ais_closest_type      TEXT,
    ais_closest_dist_km   REAL,
    ais_suggested_present INTEGER DEFAULT 0,   -- 1 if any vessel within range_km

    -- Human labels (NULL = not yet labeled)
    label_present         INTEGER,             -- 1=yes  0=no
    label_ship_type       TEXT,                -- cargo/tanker/passenger/fishing/pilot_tug/other/none
    label_noise_class     TEXT,                -- propeller/engine/biologics/background/mixed/other/none
    label_confidence      INTEGER,             -- 1=low 2=medium 3=high
    label_notes           TEXT,
    labeled_by            TEXT DEFAULT 'labeler',
    labeled_at            TEXT,
    status                TEXT DEFAULT 'pending'  -- pending/labeled/skipped
);
CREATE INDEX IF NOT EXISTS idx_lw_status  ON label_windows(status);
CREATE INDEX IF NOT EXISTS idx_lw_tstart  ON label_windows(t_start);
"""

# ── helpers ───────────────────────────────────────────────────────────────────

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin(math.radians(lat2-lat1)/2)**2
         + math.cos(phi1)*math.cos(phi2)*math.sin(math.radians(lon2-lon1)/2)**2)
    return 2*R*math.asin(math.sqrt(max(0, a)))

def parse_ts(ts):
    return datetime.strptime(ts.strip(), "%Y-%m-%d %H:%M:%S UTC")

def fmt_ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")

def get_ais_dbs():
    return [p for p in sorted(glob.glob(os.path.join(DATA_DIR, '*.db')))
            if 'labels' not in os.path.basename(p)]

def detect_table(conn):
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for pref in ('ais_data','ais_records','messages','records','data'):
        if pref in names: return pref
    return names[0] if names else None

# ── load all AIS pings once ───────────────────────────────────────────────────

def load_pings(t_start, t_end, buffer_sec=120):
    """Load every ping in [t_start-buffer, t_end+buffer] into a list of dicts."""
    t0 = fmt_ts(t_start - timedelta(seconds=buffer_sec))
    t1 = fmt_ts(t_end   + timedelta(seconds=buffer_sec))
    rows = []
    for db_path in get_ais_dbs():
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            table = detect_table(conn)
            if not table:
                conn.close(); continue
            for r in conn.execute(
                f"SELECT mmsi, ship_name, ship_type, latitude, longitude, timestamp_utc "
                f"FROM {table} WHERE timestamp_utc >= ? AND timestamp_utc <= ?", (t0, t1)
            ):
                try:
                    rows.append({
                        'mmsi':  str(r['mmsi']),
                        'name':  r['ship_name'] or '',
                        'type':  r['ship_type'] or '',
                        'lat':   float(r['latitude']),
                        'lon':   float(r['longitude']),
                        't':     parse_ts(r['timestamp_utc']),
                    })
                except (TypeError, ValueError):
                    pass
            conn.close()
        except Exception:
            pass
    return rows

# ── per-window context ────────────────────────────────────────────────────────

def window_context(midpoint, all_pings, range_km, buffer_sec=120):
    """Return AIS context dict for one window."""
    # Find pings near the midpoint for each vessel
    nearby = {}
    for p in all_pings:
        diff = abs((p['t'] - midpoint).total_seconds())
        if diff > buffer_sec:
            continue
        mmsi = p['mmsi']
        if mmsi not in nearby or diff < nearby[mmsi]['diff']:
            nearby[mmsi] = {**p, 'diff': diff}

    # Filter by audible range
    vessels = []
    for mmsi, p in nearby.items():
        dist = haversine_km(HYDROPHONE_LAT, HYDROPHONE_LON, p['lat'], p['lon'])
        if dist <= range_km:
            vessels.append({'mmsi': mmsi, 'name': p['name'], 'type': p['type'], 'dist': round(dist,2)})
    vessels.sort(key=lambda v: v['dist'])

    c = vessels[0] if vessels else None
    return {
        'vessel_count':      len(vessels),
        'closest_mmsi':      c['mmsi']  if c else None,
        'closest_name':      c['name']  if c else None,
        'closest_type':      c['type']  if c else None,
        'closest_dist_km':   c['dist']  if c else None,
        'suggested_present': 1          if c else 0,
    }

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--from',   dest='from_ts', default='2026-06-03 11:00:00 UTC')
    parser.add_argument('--to',     dest='to_ts',   default='2026-06-03 19:00:00 UTC')
    parser.add_argument('--window', type=int,       default=30)
    parser.add_argument('--range',  type=float,     default=10.0)
    parser.add_argument('--output', default=OUTPUT)
    args = parser.parse_args()

    t_start = parse_ts(args.from_ts)
    t_end   = parse_ts(args.to_ts)
    total_sec = (t_end - t_start).total_seconds()
    n_windows = int(total_sec / args.window)

    print(f"Range:   {fmt_ts(t_start)}  to  {fmt_ts(t_end)}")
    print(f"Windows: {n_windows} × {args.window}s")
    print(f"AIS range threshold: {args.range} km")

    print("Loading AIS pings…")
    all_pings = load_pings(t_start, t_end)
    print(f"  {len(all_pings)} pings loaded from {len(get_ais_dbs())} DB(s)")

    conn = sqlite3.connect(args.output)
    conn.executescript(SCHEMA)

    print("Generating windows…")
    for i in range(n_windows):
        ws  = t_start + timedelta(seconds=i * args.window)
        we  = ws + timedelta(seconds=args.window)
        mid = ws + timedelta(seconds=args.window / 2)
        ctx = window_context(mid, all_pings, args.range)

        conn.execute(
            """INSERT OR IGNORE INTO label_windows
               (t_start, t_end, duration_sec,
                ais_vessel_count, ais_closest_mmsi, ais_closest_name,
                ais_closest_type, ais_closest_dist_km, ais_suggested_present)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (fmt_ts(ws), fmt_ts(we), args.window,
             ctx['vessel_count'], ctx['closest_mmsi'], ctx['closest_name'],
             ctx['closest_type'], ctx['closest_dist_km'], ctx['suggested_present']),
        )
        if (i+1) % 100 == 0:
            conn.commit()
            pct = 100*(i+1)/n_windows
            present = ctx['suggested_present']
            print(f"  {i+1}/{n_windows} ({pct:.0f}%)  last: {'vessel' if present else 'ambient'}")

    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM label_windows").fetchone()[0]
    vessel_windows = conn.execute(
        "SELECT COUNT(*) FROM label_windows WHERE ais_suggested_present=1").fetchone()[0]
    conn.close()

    print(f"\nDone: {args.output}")
    print(f"  {total} windows total")
    print(f"  {vessel_windows} ({100*vessel_windows//total}%) with AIS vessel present")
    print(f"  {total-vessel_windows} ambient / no vessel")

if __name__ == '__main__':
    main()
