#!/usr/bin/env python3
"""
Build the ML training label database from raw AIS data.

One row per 0.5-second acoustic frame:
  - Hydrophone real position: lat=32.843153, lon=34.971938
  - AIS-interpolated vessel positions (linear between pings)
  - Distance + bearing from hydrophone to every vessel in range
  - Is vessel moving (SOG or computed speed)
  - AIS data confidence (based on ping age / interpolation gap)

Output: ais_dashboard/training_db.db
"""

import os, glob, sqlite3, math, bisect
from datetime import datetime, timedelta

# ── Config ────────────────────────────────────────────────────────────────────
HYDRO_LAT       = 32.843153
HYDRO_LON       = 34.971938

T_START_STR     = "2026-06-03 11:00:00 UTC"
T_END_STR       = "2026-06-03 19:00:00 UTC"
FRAME_SEC       = 0.5          # seconds per frame
MAX_RANGE_KM    = 15.0         # vessels beyond this distance are ignored
MIN_MOVE_KNOTS  = 0.5          # SOG threshold for "moving"

# Confidence thresholds (seconds from nearest AIS ping to frame midpoint)
HIGH_SEC    = 30.0    # <=30 s  -> high
MEDIUM_SEC  = 180.0   # <=3 min -> medium
LOW_SEC     = 600.0   # <=10 min -> low  |  >10 min -> none

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_parent  = os.path.join(BASE_DIR, '..')
DATA_DIR = (os.path.join(_parent, 'ais_data')
            if os.path.isdir(os.path.join(_parent, 'ais_data'))
            else _parent)
OUTPUT   = os.path.join(BASE_DIR, 'training_db.db')

# ── Schema ────────────────────────────────────────────────────────────────────
SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS metadata (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- One row per 0.5-second acoustic frame
CREATE TABLE IF NOT EXISTS training_frames (
    frame_idx       INTEGER PRIMARY KEY,   -- 0-based; t = T_START + frame_idx*0.5s
    t_start         TEXT NOT NULL,         -- "YYYY-MM-DD HH:MM:SS[.5] UTC"

    -- Closest vessel within MAX_RANGE_KM at this frame
    mmsi            TEXT,
    ship_name       TEXT,
    ship_type       TEXT,
    dist_km         REAL,                  -- distance from hydrophone (km)
    bearing_deg     REAL,                  -- bearing FROM hydrophone TO vessel (deg)
    vessel_lat      REAL,
    vessel_lon      REAL,

    -- Vessel motion
    sog_kn          REAL,                  -- speed over ground from AIS (knots)
    cog_deg         REAL,                  -- course over ground from AIS (deg)
    is_moving       INTEGER,               -- 1=moving  0=stationary  NULL=unknown
    move_source     TEXT,                  -- 'sog' | 'computed' | 'unknown'

    -- AIS data quality for the closest vessel
    ais_confidence  TEXT,                  -- 'high' | 'medium' | 'low' | 'none'
    ais_age_sec     REAL,                  -- seconds from nearest ping to frame midpoint
    ais_interp      TEXT,                  -- 'exact' | 'interpolated' | 'extrapolated'

    -- Frame summary
    n_vessels       INTEGER DEFAULT 0,     -- how many vessels within MAX_RANGE_KM
    vessel_present  INTEGER DEFAULT 0      -- 1 if n_vessels > 0
);

-- All vessels within range for each frame (join with training_frames on frame_idx)
CREATE TABLE IF NOT EXISTS frame_vessels (
    frame_idx       INTEGER NOT NULL,
    mmsi            TEXT    NOT NULL,
    ship_name       TEXT,
    ship_type       TEXT,
    dist_km         REAL,
    bearing_deg     REAL,
    vessel_lat      REAL,
    vessel_lon      REAL,
    sog_kn          REAL,
    cog_deg         REAL,
    is_moving       INTEGER,
    ais_confidence  TEXT,
    ais_age_sec     REAL,
    ais_interp      TEXT,
    PRIMARY KEY (frame_idx, mmsi)
);

CREATE INDEX IF NOT EXISTS idx_tf_present ON training_frames(vessel_present);
CREATE INDEX IF NOT EXISTS idx_tf_moving  ON training_frames(is_moving);
CREATE INDEX IF NOT EXISTS idx_tf_conf    ON training_frames(ais_confidence);
CREATE INDEX IF NOT EXISTS idx_fv_frame   ON frame_vessels(frame_idx);
CREATE INDEX IF NOT EXISTS idx_fv_mmsi    ON frame_vessels(mmsi);
"""

# ── Math helpers ──────────────────────────────────────────────────────────────

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    a = (math.sin(math.radians(lat2-lat1)/2)**2
         + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))
         * math.sin(math.radians(lon2-lon1)/2)**2)
    return 2*R*math.asin(math.sqrt(max(0.0, a)))

def bearing_to(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2-lon1)
    x  = math.sin(dl)*math.cos(phi2)
    y  = math.cos(phi1)*math.sin(phi2) - math.sin(phi1)*math.cos(phi2)*math.cos(dl)
    return (math.degrees(math.atan2(x, y))+360)%360

def parse_ts(s):
    return datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S UTC")

def frame_ts_str(base_dt, fi):
    """Return timestamp string for 0.5s frame index fi."""
    sec  = fi // 2
    half = fi %  2
    t = base_dt + timedelta(seconds=sec)
    s = t.strftime("%Y-%m-%d %H:%M:%S")
    return (s + ".5 UTC") if half else (s + " UTC")

def frame_midpoint(base_dt, fi):
    """Datetime of frame midpoint (frame_start + 0.25 s)."""
    ms = fi * 500 + 250        # milliseconds from base
    return base_dt + timedelta(milliseconds=ms)

# ── AIS loading ───────────────────────────────────────────────────────────────

def get_ais_dbs():
    skip = {'labels', 'training'}
    return [p for p in sorted(glob.glob(os.path.join(DATA_DIR, '*.db')))
            if not any(s in os.path.basename(p) for s in skip)]

def detect_table(conn):
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for pref in ('ais_data','ais_records','messages','records','data'):
        if pref in names: return pref
    return names[0] if names else None

def load_pings(t_start, t_end, buf_min=10):
    t0 = (t_start - timedelta(minutes=buf_min)).strftime("%Y-%m-%d %H:%M:%S UTC")
    t1 = (t_end   + timedelta(minutes=buf_min)).strftime("%Y-%m-%d %H:%M:%S UTC")
    rows = []
    for db_path in get_ais_dbs():
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            table = detect_table(conn)
            if not table: conn.close(); continue
            for r in conn.execute(
                f"SELECT mmsi, ship_name, ship_type, latitude, longitude, "
                f"sog, cog, timestamp_utc FROM {table} "
                f"WHERE timestamp_utc >= ? AND timestamp_utc <= ?", (t0, t1)
            ):
                try:
                    rows.append({
                        'mmsi': str(r['mmsi']),
                        'name': (r['ship_name'] or '').strip(),
                        'type': (r['ship_type'] or '').strip(),
                        'lat':  float(r['latitude']),
                        'lon':  float(r['longitude']),
                        'sog':  float(r['sog'])  if r['sog'] is not None else None,
                        'cog':  float(r['cog'])  if r['cog'] is not None else None,
                        't':    parse_ts(r['timestamp_utc']),
                    })
                except (TypeError, ValueError):
                    pass
            conn.close()
        except Exception as e:
            print(f"  Warning: {e}")
    return rows

def group_pings(pings):
    """Group by MMSI, sort each list by time, build parallel time arrays for bisect."""
    by_mmsi = {}
    for p in pings:
        by_mmsi.setdefault(p['mmsi'], []).append(p)
    result = {}
    for mmsi, lst in by_mmsi.items():
        lst.sort(key=lambda p: p['t'])
        result[mmsi] = {'pings': lst, 'times': [p['t'] for p in lst]}
    return result

# ── Interpolation + confidence ────────────────────────────────────────────────

def _confidence(age_sec, gap_sec=None):
    """Compute confidence string from age and optional interpolation gap."""
    if age_sec <= HIGH_SEC:
        level = 'high'
    elif age_sec <= MEDIUM_SEC:
        level = 'medium'
    elif age_sec <= LOW_SEC:
        level = 'low'
    else:
        return 'none'
    # Downgrade if interpolating across a very large gap
    if gap_sec is not None:
        if gap_sec > LOW_SEC:    level = 'low'
        elif gap_sec > MEDIUM_SEC and level == 'high': level = 'medium'
    return level

def vessel_state(vessel_data, t_mid):
    """
    Interpolate vessel position at t_mid.
    Returns a dict with lat/lon/sog/cog/is_moving/confidence/age/interp, or None.
    """
    pings = vessel_data['pings']
    times = vessel_data['times']
    if not pings:
        return None

    idx = bisect.bisect_right(times, t_mid)

    # ── Case: after all pings ──────────────────────────────────────────────
    if idx == len(pings):
        p   = pings[-1]
        age = (t_mid - p['t']).total_seconds()
        if age > LOW_SEC:
            return None     # too stale
        return _single_state(p, age, 'extrapolated')

    # ── Case: before all pings ─────────────────────────────────────────────
    if idx == 0:
        p   = pings[0]
        age = (p['t'] - t_mid).total_seconds()
        if age > LOW_SEC:
            return None
        return _single_state(p, age, 'extrapolated')

    # ── Case: bracket available ────────────────────────────────────────────
    pb = pings[idx-1]   # before
    pa = pings[idx]     # after
    gap_sec    = (pa['t'] - pb['t']).total_seconds()
    age_before = (t_mid - pb['t']).total_seconds()
    age_after  = (pa['t'] - t_mid).total_seconds()
    age        = min(age_before, age_after)

    if gap_sec <= 0 or age <= 1.0:
        p = pb if age_before <= age_after else pa
        return _single_state(p, age, 'exact')

    # Linear interpolation
    ratio = age_before / gap_sec
    lat = pb['lat'] + ratio*(pa['lat'] - pb['lat'])
    lon = pb['lon'] + ratio*(pa['lon'] - pb['lon'])

    # SOG / COG from nearest ping
    p_near = pb if age_before <= age_after else pa
    sog, cog = p_near['sog'], p_near['cog']

    # Movement
    if sog is not None:
        is_moving = int(sog >= MIN_MOVE_KNOTS)
        move_src  = 'sog'
    else:
        comp_km  = haversine_km(pb['lat'], pb['lon'], pa['lat'], pa['lon'])
        comp_spd = (comp_km / gap_sec) * 3600 / 1.852 if gap_sec > 0 else 0.0
        is_moving = int(comp_spd >= MIN_MOVE_KNOTS)
        move_src  = 'computed'

    return {
        'lat': lat, 'lon': lon,
        'sog': sog, 'cog': cog,
        'is_moving': is_moving, 'move_src': move_src,
        'ais_confidence': _confidence(age, gap_sec),
        'ais_age_sec':    round(age, 1),
        'ais_interp':     'interpolated',
        'name': p_near['name'], 'type': p_near['type'],
    }


def _single_state(p, age_sec, interp):
    if p['sog'] is not None:
        is_moving = int(p['sog'] >= MIN_MOVE_KNOTS)
        move_src  = 'sog'
    else:
        is_moving = None
        move_src  = 'unknown'
    return {
        'lat': p['lat'], 'lon': p['lon'],
        'sog': p['sog'], 'cog': p['cog'],
        'is_moving': is_moving, 'move_src': move_src,
        'ais_confidence': _confidence(age_sec),
        'ais_age_sec':    round(age_sec, 1),
        'ais_interp':     interp,
        'name': p['name'], 'type': p['type'],
    }

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    t_start  = parse_ts(T_START_STR)
    t_end    = parse_ts(T_END_STR)
    n_frames = int((t_end - t_start).total_seconds() / FRAME_SEC)

    print(f"Hydrophone : {HYDRO_LAT}, {HYDRO_LON}")
    print(f"Period     : {T_START_STR}  to  {T_END_STR}")
    print(f"Frames     : {n_frames} x {FRAME_SEC}s = {n_frames*FRAME_SEC/3600:.1f} h")
    print(f"Max range  : {MAX_RANGE_KM} km")
    print()

    print("Loading AIS pings...")
    all_pings = load_pings(t_start, t_end)
    by_mmsi   = group_pings(all_pings)
    print(f"  {len(all_pings)} pings  |  {len(by_mmsi)} vessels")
    print()

    if os.path.exists(OUTPUT):
        os.remove(OUTPUT)
    conn = sqlite3.connect(OUTPUT)
    conn.executescript(SCHEMA)
    conn.executemany("INSERT OR REPLACE INTO metadata VALUES (?,?)", {
        'hydrophone_lat':    str(HYDRO_LAT),
        'hydrophone_lon':    str(HYDRO_LON),
        't_start':           T_START_STR,
        't_end':             T_END_STR,
        'frame_sec':         str(FRAME_SEC),
        'n_frames':          str(n_frames),
        'max_range_km':      str(MAX_RANGE_KM),
        'min_move_knots':    str(MIN_MOVE_KNOTS),
        'ais_vessels':       str(len(by_mmsi)),
    }.items())
    conn.commit()

    BATCH    = 5000
    f_batch  = []   # training_frames rows
    v_batch  = []   # frame_vessels rows
    n_present = 0

    print("Building frames...")
    for fi in range(n_frames):
        t_mid = frame_midpoint(t_start, fi)
        ts    = frame_ts_str(t_start, fi)

        in_range = []
        for mmsi, vd in by_mmsi.items():
            st = vessel_state(vd, t_mid)
            if st is None or st['ais_confidence'] == 'none':
                continue
            dist = haversine_km(HYDRO_LAT, HYDRO_LON, st['lat'], st['lon'])
            if dist > MAX_RANGE_KM:
                continue
            bear = bearing_to(HYDRO_LAT, HYDRO_LON, st['lat'], st['lon'])
            in_range.append({
                'mmsi': mmsi, **st,
                'dist_km':     round(dist, 4),
                'bearing_deg': round(bear, 2),
            })

        in_range.sort(key=lambda v: v['dist_km'])
        n_v  = len(in_range)
        pres = 1 if n_v > 0 else 0
        if pres: n_present += 1

        c = in_range[0] if in_range else {}
        f_batch.append((
            fi, ts,
            c.get('mmsi'),    c.get('name'),    c.get('type'),
            c.get('dist_km'), c.get('bearing_deg'),
            c.get('lat'),     c.get('lon'),
            c.get('sog'),     c.get('cog'),
            c.get('is_moving'), c.get('move_src'),
            c.get('ais_confidence'), c.get('ais_age_sec'), c.get('ais_interp'),
            n_v, pres,
        ))

        for v in in_range:
            v_batch.append((
                fi, v['mmsi'], v['name'], v['type'],
                v['dist_km'], v['bearing_deg'], v['lat'], v['lon'],
                v['sog'], v['cog'], v['is_moving'],
                v['ais_confidence'], v['ais_age_sec'], v['ais_interp'],
            ))

        if len(f_batch) >= BATCH:
            conn.executemany(
                "INSERT INTO training_frames VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                f_batch)
            conn.executemany(
                "INSERT INTO frame_vessels VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                v_batch)
            conn.commit()
            f_batch.clear(); v_batch.clear()
            pct = 100*(fi+1)//n_frames
            print(f"  {fi+1:>6}/{n_frames}  {pct:>3}%   present={n_present}  ({100*n_present//(fi+1)}%)")

    if f_batch:
        conn.executemany(
            "INSERT INTO training_frames VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            f_batch)
        conn.executemany(
            "INSERT INTO frame_vessels VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            v_batch)
        conn.commit()

    # ── Final stats ───────────────────────────────────────────────────────────
    tot     = conn.execute("SELECT COUNT(*) FROM training_frames").fetchone()[0]
    pres    = conn.execute("SELECT SUM(vessel_present) FROM training_frames").fetchone()[0] or 0
    moving  = conn.execute("SELECT COUNT(*) FROM training_frames WHERE is_moving=1").fetchone()[0]
    stationary = conn.execute("SELECT COUNT(*) FROM training_frames WHERE is_moving=0").fetchone()[0]
    conf = dict(conn.execute(
        "SELECT ais_confidence, COUNT(*) FROM training_frames WHERE vessel_present=1 "
        "GROUP BY ais_confidence").fetchall())
    n_fv    = conn.execute("SELECT COUNT(*) FROM frame_vessels").fetchone()[0]
    conn.close()

    print()
    print(f"Output     : {OUTPUT}")
    print(f"Frames     : {tot}")
    print(f"  Vessel present : {pres}  ({100*pres//tot}%)")
    print(f"  No vessel      : {tot-pres}  ({100*(tot-pres)//tot}%)")
    print(f"  Moving         : {moving}  |  Stationary: {stationary}")
    print(f"  Confidence (present frames): {conf}")
    print(f"  frame_vessels rows : {n_fv}")

if __name__ == '__main__':
    main()
