import os
import glob
import sqlite3
import math
import csv
import io
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, send_from_directory, Response
from flask_cors import CORS

app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)

HYDROPHONE_LAT = 32.843153
HYDROPHONE_LON = 34.971938
# Brief specifies ../ais_data/; fall back to parent folder if that doesn't exist
_base = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = (
    os.path.join(_base, '..', 'ais_data')
    if os.path.isdir(os.path.join(_base, '..', 'ais_data'))
    else os.path.join(_base, '..')
)
LABELS_DB   = os.path.join(_base, 'labels.db')
TRAINING_DB = os.path.join(_base, 'training_db.db')
GAP_THRESHOLD_SEC = 180
JUMP_THRESHOLD_KNOTS = 40


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(max(0.0, a)))


def bearing_deg(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def parse_ts(ts_str):
    return datetime.strptime(ts_str.strip(), "%Y-%m-%d %H:%M:%S UTC")


def get_db_files(db_filter=None):
    if db_filter:
        path = os.path.join(DATA_DIR, os.path.basename(db_filter))
        return [path] if os.path.isfile(path) else []
    return sorted(glob.glob(os.path.join(DATA_DIR, '*.db')))


def detect_table(conn):
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    names = [r[0] for r in cur.fetchall()]
    for preferred in ('ais_data', 'ais_records', 'messages', 'records', 'data'):
        if preferred in names:
            return preferred
    return names[0] if names else None


def query_all_dbs(sql_template, params=(), db_filter=None):
    rows = []
    for db_path in get_db_files(db_filter):
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            table = detect_table(conn)
            if not table:
                conn.close()
                continue
            sql = sql_template.format(table=table)
            cur = conn.execute(sql, params)
            rows.extend([dict(r) for r in cur.fetchall()])
            conn.close()
        except Exception:
            pass
    return rows


@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


@app.route('/api/files')
def api_files():
    return jsonify([os.path.basename(f) for f in get_db_files()])


@app.route('/api/vessels')
def api_vessels():
    db      = request.args.get('db')
    from_ts = request.args.get('from')
    to_ts   = request.args.get('to')

    sql    = "SELECT mmsi, ship_name, ship_type, COUNT(*) as ping_count FROM {table}"
    params = []
    if from_ts or to_ts:
        clauses = []
        if from_ts: clauses.append("timestamp_utc >= ?"); params.append(from_ts)
        if to_ts:   clauses.append("timestamp_utc <= ?"); params.append(to_ts)
        sql += " WHERE " + " AND ".join(clauses)
    sql += " GROUP BY mmsi HAVING ping_count > 0"

    rows = query_all_dbs(sql, tuple(params), db_filter=db)
    vessels = {}
    for r in rows:
        mmsi = r['mmsi']
        if mmsi not in vessels:
            vessels[mmsi] = {
                'mmsi': mmsi,
                'name': r['ship_name'] or '',
                'type': r['ship_type'] or '',
                'ping_count': 0,
            }
        vessels[mmsi]['ping_count'] += r['ping_count']
        if not vessels[mmsi]['name'] and r['ship_name']:
            vessels[mmsi]['name'] = r['ship_name']
    return jsonify(list(vessels.values()))


@app.route('/api/tracks')
def api_tracks():
    mmsi = request.args.get('mmsi')
    from_ts = request.args.get('from')
    to_ts = request.args.get('to')
    db = request.args.get('db')

    if not mmsi:
        return jsonify([])

    sql = (
        "SELECT timestamp_utc, mmsi, latitude, longitude, sog, cog, ship_name, ship_type "
        "FROM {table} WHERE mmsi=?"
    )
    params = [mmsi]
    if from_ts:
        sql += " AND timestamp_utc >= ?"
        params.append(from_ts)
    if to_ts:
        sql += " AND timestamp_utc <= ?"
        params.append(to_ts)
    sql += " ORDER BY timestamp_utc"

    rows = query_all_dbs(sql, tuple(params), db_filter=db)

    seen = set()
    unique_rows = []
    for r in rows:
        key = r['timestamp_utc']
        if key not in seen:
            seen.add(key)
            unique_rows.append(r)
    unique_rows.sort(key=lambda r: r['timestamp_utc'])

    result = []
    for r in unique_rows:
        lat, lon = r['latitude'], r['longitude']
        if lat is None or lon is None:
            continue
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            continue
        dist = haversine_km(HYDROPHONE_LAT, HYDROPHONE_LON, lat, lon)
        bear = bearing_deg(HYDROPHONE_LAT, HYDROPHONE_LON, lat, lon)
        result.append({
            'timestamp': r['timestamp_utc'],
            'mmsi': r['mmsi'],
            'lat': lat,
            'lon': lon,
            'sog': r['sog'],
            'cog': r['cog'],
            'ship_name': r['ship_name'],
            'ship_type': r['ship_type'],
            'distance_km': round(dist, 3),
            'bearing_deg': round(bear, 1),
        })
    return jsonify(result)


@app.route('/api/quality')
def api_quality():
    db = request.args.get('db')
    rows = query_all_dbs(
        "SELECT mmsi, ship_name, timestamp_utc, latitude, longitude "
        "FROM {table} ORDER BY mmsi, timestamp_utc",
        db_filter=db,
    )

    by_mmsi = {}
    for r in rows:
        by_mmsi.setdefault(r['mmsi'], []).append(r)

    gaps = []
    jumps = []

    for mmsi, pings in by_mmsi.items():
        seen_ts = set()
        unique_pings = []
        for p in pings:
            if p['timestamp_utc'] not in seen_ts:
                seen_ts.add(p['timestamp_utc'])
                unique_pings.append(p)
        unique_pings.sort(key=lambda p: p['timestamp_utc'])

        for i in range(1, len(unique_pings)):
            prev = unique_pings[i - 1]
            curr = unique_pings[i]
            try:
                t0 = parse_ts(prev['timestamp_utc'])
                t1 = parse_ts(curr['timestamp_utc'])
                dt_sec = (t1 - t0).total_seconds()
                if dt_sec <= 0:
                    continue

                if dt_sec > GAP_THRESHOLD_SEC:
                    gaps.append({
                        'mmsi': mmsi,
                        'ship_name': curr.get('ship_name') or '',
                        'from': prev['timestamp_utc'],
                        'to': curr['timestamp_utc'],
                        'duration_sec': int(dt_sec),
                        'lat': curr['latitude'],
                        'lon': curr['longitude'],
                    })

                if (
                    curr['latitude'] and curr['longitude']
                    and prev['latitude'] and prev['longitude']
                ):
                    dist_km = haversine_km(
                        float(prev['latitude']), float(prev['longitude']),
                        float(curr['latitude']), float(curr['longitude']),
                    )
                    speed_knots = (dist_km / dt_sec) * 3600 / 1.852
                    if speed_knots > JUMP_THRESHOLD_KNOTS:
                        jumps.append({
                            'mmsi': mmsi,
                            'ship_name': curr.get('ship_name') or '',
                            'timestamp': curr['timestamp_utc'],
                            'speed_knots': round(speed_knots, 1),
                            'lat': curr['latitude'],
                            'lon': curr['longitude'],
                        })
            except Exception:
                pass

    return jsonify({'gaps': gaps, 'jumps': jumps})


# ── Labeling API ──────────────────────────────────────────────────────────────

def labels_conn():
    if not os.path.isfile(LABELS_DB):
        return None
    conn = sqlite3.connect(LABELS_DB)
    conn.row_factory = sqlite3.Row
    return conn


@app.route('/labeling')
def labeling_page():
    return send_from_directory('static', 'labeling.html')


@app.route('/api/labels/stats')
def labels_stats():
    conn = labels_conn()
    if not conn:
        return jsonify({'error': 'labels.db not found — run generate_windows.py first'}), 404
    stats = dict(conn.execute(
        "SELECT COUNT(*) total, "
        "SUM(status='labeled') labeled, "
        "SUM(status='pending') pending, "
        "SUM(status='skipped') skipped "
        "FROM label_windows"
    ).fetchone())
    conn.close()
    return jsonify(stats)


@app.route('/api/labels/windows')
def labels_windows():
    conn = labels_conn()
    if not conn:
        return jsonify([])
    status = request.args.get('status', 'pending')
    offset = int(request.args.get('offset', 0))
    limit  = int(request.args.get('limit', 50))
    rows = conn.execute(
        "SELECT * FROM label_windows WHERE status=? ORDER BY t_start LIMIT ? OFFSET ?",
        (status, limit, offset)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/labels/window/<int:win_id>')
def label_window(win_id):
    conn = labels_conn()
    if not conn:
        return jsonify({'error': 'labels.db not found'}), 404
    row = conn.execute("SELECT * FROM label_windows WHERE id=?", (win_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'not found'}), 404
    result = dict(row)
    conn.close()

    # Live AIS context: vessels within 15 km at this window (±2 min)
    t_start = parse_ts(result['t_start'])
    t_end   = parse_ts(result['t_end'])
    mid     = t_start + (t_end - t_start) / 2
    buf     = timedelta(seconds=120)
    t0_str  = (mid - buf).strftime("%Y-%m-%d %H:%M:%S UTC")
    t1_str  = (mid + buf).strftime("%Y-%m-%d %H:%M:%S UTC")

    context_rows = query_all_dbs(
        "SELECT mmsi, ship_name, ship_type, latitude, longitude, timestamp_utc "
        "FROM {table} WHERE timestamp_utc >= ? AND timestamp_utc <= ?",
        (t0_str, t1_str),
    )
    by_mmsi = {}
    for p in context_rows:
        mmsi = str(p['mmsi'])
        try:
            t = parse_ts(p['timestamp_utc'])
            diff = abs((t - mid).total_seconds())
            if mmsi not in by_mmsi or diff < by_mmsi[mmsi]['diff']:
                by_mmsi[mmsi] = {**p, 'diff': diff}
        except Exception:
            pass

    nearby = []
    for mmsi, p in by_mmsi.items():
        try:
            dist = haversine_km(HYDROPHONE_LAT, HYDROPHONE_LON,
                                float(p['latitude']), float(p['longitude']))
            bear = bearing_deg(HYDROPHONE_LAT, HYDROPHONE_LON,
                               float(p['latitude']), float(p['longitude']))
            nearby.append({
                'mmsi': mmsi,
                'name': p.get('ship_name') or '',
                'type': p.get('ship_type') or '',
                'dist_km': round(dist, 2),
                'bearing_deg': round(bear, 1),
            })
        except (TypeError, ValueError):
            pass
    nearby.sort(key=lambda x: x['dist_km'])
    result['ais_context'] = nearby
    return jsonify(result)


@app.route('/api/labels/save/<int:win_id>', methods=['POST'])
def label_save(win_id):
    conn = labels_conn()
    if not conn:
        return jsonify({'error': 'labels.db not found'}), 404
    data = request.get_json()
    now  = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    conn.execute(
        """UPDATE label_windows SET
           label_present=?, label_ship_type=?, label_noise_class=?,
           label_confidence=?, label_notes=?, labeled_by=?, labeled_at=?, status='labeled'
           WHERE id=?""",
        (data.get('present'), data.get('ship_type'), data.get('noise_class'),
         data.get('confidence'), data.get('notes', ''), data.get('labeled_by', 'labeler'),
         now, win_id)
    )
    conn.commit()
    # Return next pending window id
    nxt = conn.execute(
        "SELECT id FROM label_windows WHERE status='pending' AND id>? ORDER BY id LIMIT 1",
        (win_id,)
    ).fetchone()
    conn.close()
    return jsonify({'ok': True, 'next_id': nxt[0] if nxt else None})


@app.route('/api/labels/skip/<int:win_id>', methods=['POST'])
def label_skip(win_id):
    conn = labels_conn()
    if not conn:
        return jsonify({'error': 'labels.db not found'}), 404
    conn.execute("UPDATE label_windows SET status='skipped' WHERE id=?", (win_id,))
    conn.commit()
    nxt = conn.execute(
        "SELECT id FROM label_windows WHERE status='pending' AND id>? ORDER BY id LIMIT 1",
        (win_id,)
    ).fetchone()
    conn.close()
    return jsonify({'ok': True, 'next_id': nxt[0] if nxt else None})


@app.route('/api/labels/export.csv')
def labels_export():
    conn = labels_conn()
    if not conn:
        return 'labels.db not found', 404
    rows = conn.execute(
        "SELECT * FROM label_windows WHERE status='labeled' ORDER BY t_start"
    ).fetchall()
    conn.close()
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows([dict(r) for r in rows])
    return Response(buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=labels.csv'})


# ── Training DB API ───────────────────────────────────────────────────────────

def tdb_conn():
    if not os.path.isfile(TRAINING_DB):
        return None
    conn = sqlite3.connect(TRAINING_DB)
    conn.row_factory = sqlite3.Row
    return conn


@app.route('/training')
def training_page():
    return send_from_directory('static', 'training_dashboard.html')


@app.route('/api/training/stats')
def training_stats():
    conn = tdb_conn()
    if not conn:
        return jsonify({'error': 'training_db.db not found'}), 404
    meta  = dict(conn.execute("SELECT key,value FROM metadata").fetchall())
    total = conn.execute("SELECT COUNT(*) FROM training_frames").fetchone()[0]
    pres  = conn.execute("SELECT SUM(vessel_present) FROM training_frames").fetchone()[0] or 0
    mov   = conn.execute("SELECT COUNT(*) FROM training_frames WHERE is_moving=1").fetchone()[0]
    uniq  = conn.execute("SELECT COUNT(DISTINCT mmsi) FROM frame_vessels").fetchone()[0]
    conf  = dict(conn.execute(
        "SELECT ais_confidence, COUNT(*) FROM training_frames "
        "WHERE vessel_present=1 GROUP BY ais_confidence").fetchall())
    conn.close()
    return jsonify({**meta, 'total_frames': total, 'frames_present': pres,
                    'frames_moving': mov, 'unique_vessels': uniq, 'confidence': conf})


@app.route('/api/training/vessels')
def training_vessels():
    conn = tdb_conn()
    if not conn:
        return jsonify([])
    rows = conn.execute("""
        SELECT
            fv.mmsi,
            MAX(CASE WHEN fv.ship_name!='' THEN fv.ship_name END) AS ship_name,
            MAX(CASE WHEN fv.ship_type!='' THEN fv.ship_type END) AS ship_type,
            COUNT(*)                                               AS frame_count,
            MIN(tf.t_start)                                        AS first_seen,
            MAX(tf.t_start)                                        AS last_seen,
            ROUND(MIN(fv.dist_km), 3)                             AS min_dist_km,
            ROUND(AVG(fv.dist_km), 3)                             AS avg_dist_km,
            ROUND(MAX(fv.dist_km), 3)                             AS max_dist_km,
            ROUND(AVG(CASE WHEN fv.sog_kn IS NOT NULL
                           THEN fv.sog_kn END), 2)                AS avg_sog,
            ROUND(MAX(fv.sog_kn), 2)                              AS max_sog,
            ROUND(100.0 * SUM(CASE WHEN fv.is_moving=1
                                   THEN 1 ELSE 0 END)
                        / COUNT(*), 1)                            AS pct_moving
        FROM frame_vessels fv
        JOIN training_frames tf ON fv.frame_idx = tf.frame_idx
        GROUP BY fv.mmsi
        ORDER BY frame_count DESC
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/training/vessel/<mmsi>')
def training_vessel_track(mmsi):
    conn = tdb_conn()
    if not conn:
        return jsonify([])
    # Downsample: 1 point per 10 seconds (every 20 frames)
    ds = int(request.args.get('ds', 20))
    rows = conn.execute("""
        SELECT
            fv.frame_idx,
            tf.t_start,
            fv.dist_km,
            fv.bearing_deg,
            fv.sog_kn,
            fv.cog_deg,
            fv.is_moving,
            fv.ais_confidence,
            fv.vessel_lat,
            fv.vessel_lon
        FROM frame_vessels fv
        JOIN training_frames tf ON fv.frame_idx = tf.frame_idx
        WHERE fv.mmsi = ?
          AND fv.frame_idx % ? = 0
        ORDER BY fv.frame_idx
    """, (mmsi, ds)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5050, debug=True)
