# AIS Trajectory Dashboard — Build Brief

## What to build
A local web dashboard (Python backend + HTML/JS frontend) to visualize
AIS vessel data collected from SQLite databases in the `ais_data/` folder.

## Backend — `dashboard.py`
- Use Flask (or Fastapi) with these endpoints:
  - `GET /api/vessels` — list of all unique vessels (mmsi, name, type, ping count)
  - `GET /api/tracks?mmsi=...&from=...&to=...` — time-filtered positions for a vessel
  - `GET /api/quality` — data quality report (gaps, jumps, coverage)
  - `GET /api/files` — list available .db files in ais_data/
- Auto-detect and merge all .db files in `ais_data/` folder
- Quality analysis logic:
  - GAP = interval between consecutive pings for same MMSI > 180 seconds
  - JUMP = implied speed between consecutive pings > 40 knots (use haversine)
  - Return gap timestamps, durations, and vessel MMSI
- Hydrophone reference point: lat=32.8447770, lon=34.9571940
  - Compute distance_km and bearing_deg from hydrophone to vessel for every ping
- Run on localhost:5050

## Frontend — `static/index.html`
Single HTML file using Leaflet.js (CDN) for the map and Chart.js (CDN) for charts.

### Layout
- Top bar: title, stats pills (vessel count, ping count, gap count, jump count)
- Left: Leaflet map (70% width) showing vessel trajectories
  - Each vessel a different color polyline
  - Moving dot marker showing current position during playback
  - Orange circle markers at gap locations
  - Red circle markers at position jumps
  - Fixed red marker at hydrophone position (32.8447770, 34.9571940)
- Right sidebar (30% width):
  - DB file selector dropdown (from /api/files)
  - Vessel list with per-vessel quality badge (clean/warn/bad)
  - Data quality summary cards (clean %, avg interval, gap count, jump count)
  - Issues list (gaps and jumps sorted by time)
  - Selected vessel detail panel (name, mmsi, type, pings, distance to hydrophone)
- Bottom: timeline scrubber (play/pause, 1x/5x/20x speed, time display)
- Bottom right: distance-to-hydrophone mini line chart (Chart.js) for selected vessel

### Data quality color coding
- Green = clean
- Orange = gap warning
- Red = position jump

## File structure to create
ais_dashboard/
  dashboard.py
  requirements.txt       # flask, haversine (or math only)
  static/
    index.html

## Important notes
- All AIS data is already collected in ais_data/*.db (outside this folder)
- Path to data: ../ais_data/ relative to ais_dashboard/
- The dashboard is read-only, never writes to the database
- Timestamps in DB are stored as TEXT: "YYYY-MM-DD HH:MM:SS UTC"
  Parse them with: datetime.strptime(ts, "%Y-%m-%d %H:%M:%S UTC")
- SQLite schema:
    id, timestamp_utc, mmsi, ship_name, latitude, longitude,
    sog, cog, ship_type, imo, call_sign, destination, raw_json