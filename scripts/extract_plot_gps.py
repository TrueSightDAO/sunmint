#!/usr/bin/env python3
"""Extract GPS from boundary photos/videos and upsert the plot record.

Reads the GPS coordinates embedded in uploaded boundary media (exiftool reads
container metadata - never decode video frames), builds the boundary polygon as
the convex hull of the GPS points (monotonic chain, stdlib only), labels it
``approx``, and upserts the plot row in the SunMint Farms sheet (create if the
plot_id doesn't exist, update the polygon/coordinates + media if it does).

Boundary authority tiers (per SUNMINT_PLOTS_REGISTRY.md):
    approx   - hull of photo/video GPS points only (quick sketch; this script's default)
    gps_walk - perimeter walk with a GPS-track app (real polygon)
    car/incra - farmer's CAR/INCRA registration (authoritative)

Usage:
  python3 scripts/extract_plot_gps.py --media /path/to/boundary/photos/ \
      --plot-id LD-P1 --farm-id paulo-la-do-sitio --hectares 5.56 \
      --owner "Paulo" --region "Para" --name "Paulo La do Sitio"
  # --dry-run to preview without writing the sheet
  # --append-media to ADD the new media to an existing plot's media list (default: replace)
  # --no-hull to write the raw GPS points as the ring instead of a convex hull
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Same spreadsheet as build_plots_geojson.py
SHEET_ID = "1qbZZhf-_7xzmDTriaJVWj6OZshyQsFkdsAV8-pyzASQ"
SHEET_TAB = "SunMint Plots"

# Credential resolution: mirrors google_creds.py convention.
DEFAULT_CREDS_DIR = "/opt/truesight_autopilot/config/google"
SA_NAME = "agroverse_qr_code_manager"  # has write access to the Farms sheet

SHEET_WRITE_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

# Exiftool DMS output: 3 deg 17' 45.96" S   /   52 deg 34' 59.39" W
_DMS_RE = re.compile(
    r"^\s*(?P<deg>\d+(?:\.\d+)?)\s*deg\s*(?P<min>\d+(?:\.\d+)?)?\s*'?\s*"
    r"(?P<sec>\d+(?:\.\d+)?)?\s*\"?\s*(?P<ref>[NSEW])\s*$",
    re.IGNORECASE,
)


def dms_to_decimal(raw: str) -> float | None:
    """Convert '3 deg 17' 45.96\" S' (or plain decimal) to signed decimal degrees."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    # Plain decimal already
    try:
        return float(raw)
    except ValueError:
        pass
    m = _DMS_RE.match(raw)
    if not m:
        return None
    deg = float(m.group("deg"))
    minutes = float(m.group("min") or 0)
    sec = float(m.group("sec") or 0)
    val = deg + minutes / 60.0 + sec / 3600.0
    ref = m.group("ref").upper()
    if ref in ("S", "W"):
        val = -val
    return round(val, 6)


def extract_gps_points(media_paths: list[str]) -> list[tuple[float, float]]:
    """Return [(lat, lng), ...] for every file with readable GPS, in order."""
    points: list[tuple[float, float]] = []
    for p in media_paths:
        if not os.path.isfile(p):
            print(f"  skip (missing): {p}")
            continue
        try:
            out = subprocess.run(
                [
                    "exiftool",
                    "-GPSLatitude",
                    "-GPSLatitudeRef",
                    "-GPSLongitude",
                    "-GPSLongitudeRef",
                    "-s",
                    p,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"  skip (exiftool err): {p}: {e}")
            continue
        lat = lng = None
        ref = {"lat": "N", "lng": "E"}
        for line in out.stdout.splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            tag, _, val = line.partition(":")
            tag = tag.strip()
            val = val.strip()
            if tag == "GPSLatitude":
                lat = dms_to_decimal(val)
            elif tag == "GPSLongitude":
                lng = dms_to_decimal(val)
            elif tag == "GPSLatitudeRef":
                ref["lat"] = val.strip().upper()[:1] or "N"
            elif tag == "GPSLongitudeRef":
                ref["lng"] = val.strip().upper()[:1] or "E"
        if lat is None or lng is None:
            print(f"  no GPS: {p}")
            continue
        if ref["lat"] in ("S",):
            lat = -abs(lat)
        if ref["lng"] in ("W",):
            lng = -abs(lng)
        points.append((lat, lng))
        print(f"  + {p}: {lat:.6f}, {lng:.6f}")
    return points


def convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Monotonic-chain convex hull (stdlib only). Returns hull CCW, no duplicate last pt."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def build_ring(
    points: list[tuple[float, float]], hull: bool = True
) -> list[list[float]]:
    """GeoJSON closed ring [lng, lat] order. First == last."""
    if hull:
        pts = convex_hull(points)
    else:
        pts = list(points)
    ring = [[round(lng, 6), round(lat, 6)] for lat, lng in pts]
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def get_creds():
    from google.oauth2 import service_account

    creds_dir = os.environ.get("GOOGLE_CREDS_DIR", DEFAULT_CREDS_DIR)
    path = Path(creds_dir) / f"{SA_NAME}_gdrive_key.json"
    if not path.is_file():
        sys.exit(f"credential file not found: {path}")
    return service_account.Credentials.from_service_account_file(
        str(path), scopes=[SHEET_WRITE_SCOPE]
    )


def get_worksheet(creds):
    import gspread

    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID).worksheet(SHEET_TAB)


def header_index(ws):
    header = ws.row_values(1)
    hl = [(h or "").strip().lower() for h in header]

    def find(names):
        for n in names:
            n = n.strip().lower()
            if not n:
                continue
            for i, h in enumerate(hl):
                if h == n:
                    return i
            for i, h in enumerate(hl):
                if h.startswith(n):
                    return i
        return None

    return {
        "plot_id": find(["plot id", "plot"]),
        "farm_id": find(["farm id", "farm"]),
        "name": find(["plot name", "name", "site name"]),
        "hectares": find(["hectares", "area ha", "area"]),
        "status": find(["status"]),
        "boundary_authority": find(["boundary authority", "authority"]),
        "owner": find(["owner", "family", "farmer"]),
        "region": find(["region", "state", "municipality"]),
        "verified_at": find(["verified at", "verified date"]),
        "media": find(["media", "media urls", "photo urls"]),
        "notes": find(["notes", "comments"]),
        "coordinates": find(["coordinates", "polygon", "coords", "geometry"]),
    }


def load_rows(ws, cols):
    rows = ws.get_all_values()
    if not rows:
        return []
    return rows[1:]


def find_row(rows, cols, plot_id):
    i = cols["plot_id"]
    if i is None:
        return None
    for r_i, row in enumerate(rows):
        if (row[i] if i < len(row) else "").strip() == plot_id:
            return r_i
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--media",
        nargs="+",
        required=True,
        help="paths to boundary photos/videos (dirs allowed)",
    )
    ap.add_argument("--plot-id", required=True, help="e.g. LD-P1")
    ap.add_argument("--farm-id", default="", help="e.g. paulo-la-do-sitio")
    ap.add_argument("--name", default="", help="plot display name")
    ap.add_argument("--hectares", default="", help="area in ha (optional)")
    ap.add_argument("--owner", default="", help="owner / family")
    ap.add_argument("--region", default="", help="state / municipality")
    ap.add_argument("--status", default="proposed", help="proposed|planted|verified")
    ap.add_argument(
        "--boundary-authority",
        default="approx",
        help="approx (default) | gps_walk | car | incra",
    )
    ap.add_argument(
        "--append-media",
        action="store_true",
        help="append new media to existing plot's media list (default: replace)",
    )
    ap.add_argument(
        "--no-hull",
        action="store_true",
        help="use raw GPS points as ring instead of convex hull",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="compute and print everything, don't write the sheet",
    )
    args = ap.parse_args()

    # Expand directories to their image/video files.
    media_paths: list[str] = []
    for p in args.media:
        if os.path.isdir(p):
            for f in sorted(Path(p).iterdir()):
                if f.suffix.lower() in {
                    ".jpg",
                    ".jpeg",
                    ".heic",
                    ".mov",
                    ".mp4",
                    ".png",
                    ".gif",
                }:
                    media_paths.append(str(f))
        else:
            media_paths.append(p)

    print(f"=== extracting GPS from {len(media_paths)} files ===")
    points = extract_gps_points(media_paths)
    if not points:
        sys.exit("no GPS found in any media file - aborting (nothing to write)")

    print(f"=== {len(points)} GPS points ===")
    for lat, lng in points:
        print(f"  {lat:.6f}, {lng:.6f}")
    distinct = set((round(lat, 6), round(lng, 6)) for lat, lng in points)
    if len(distinct) < 3:
        sys.exit(
            f"only {len(distinct)} distinct GPS point(s) - need at least 3 to form a "
            "boundary polygon. Ask the farmer to photograph more boundary markers "
            "(e.g. the pillar, the log, road corners)."
        )
    ring = build_ring(points, hull=not args.no_hull)
    print(f"=== boundary ring ({len(ring)} vertices) ===")
    print(json.dumps(ring))

    coords_json = json.dumps(ring)

    # Media: keep repo-relative paths (images/<plot_id>/...); collapse absolute
    # local temp paths to basenames (operator moves files into the repo).
    def _media_ref(p: str) -> str:
        ap = os.path.abspath(p)
        if ap.startswith(("/tmp/", "/home/", "/var/")):
            return os.path.basename(ap)
        return p

    media_joined = "; ".join(_media_ref(p) for p in media_paths)

    if args.dry_run:
        print("\n=== DRY RUN (no sheet write) ===")
        print(f"plot_id={args.plot_id} farm_id={args.farm_id}")
        print(f"name={args.name} hectares={args.hectares} status={args.status}")
        print(
            f"boundary_authority={args.boundary_authority} owner={args.owner} region={args.region}"
        )
        print(f"media={media_joined}")
        print(f"coordinates={coords_json}")
        return

    creds = get_creds()
    ws = get_worksheet(creds)
    cols = header_index(ws)
    rows = load_rows(ws, cols)

    row_i = find_row(rows, cols, args.plot_id)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def set_cell(r, col_name, value):
        ci = cols[col_name]
        if ci is None:
            print(f"  WARN: no '{col_name}' column; cannot write")
            return
        while len(ws.row_values(r + 1)) < ci + 1:
            ws.update_cell(r + 1, len(ws.row_values(r + 1)) + 1, "")
        ws.update_cell(r + 1, ci + 1, value)

    if row_i is None:
        # New plot: append a row.
        print(f"=== creating new plot row {args.plot_id} ===")
        next_row = len(rows) + 2  # 1-based, after header + data
        # Write all columns via row indices:
        for col_name, value in [
            ("plot_id", args.plot_id),
            ("farm_id", args.farm_id),
            ("name", args.name or args.plot_id),
            ("hectares", args.hectares),
            ("status", args.status),
            ("boundary_authority", args.boundary_authority),
            ("owner", args.owner),
            ("region", args.region),
            ("verified_at", ""),
            ("media", media_joined),
            ("notes", ""),
            ("coordinates", coords_json),
        ]:
            set_cell(next_row - 1, col_name, value)
        print(f"  created row {next_row}")
    else:
        r = row_i  # 0-based data row
        print(f"=== updating existing plot {args.plot_id} (data row {r + 2}) ===")
        existing_media = ""
        if args.append_media:
            mi = cols["media"]
            if mi is not None and mi < len(rows[r]):
                existing_media = rows[r][mi]
            if existing_media:
                media_joined = existing_media + "; " + media_joined
        for col_name, value in [
            ("farm_id", args.farm_id),
            ("name", args.name),
            ("hectares", args.hectares),
            ("status", args.status),
            ("boundary_authority", args.boundary_authority),
            ("owner", args.owner),
            ("region", args.region),
            ("verified_at", now if args.boundary_authority != "approx" else ""),
            ("media", media_joined),
            ("coordinates", coords_json),
        ]:
            if value == "":
                continue
            set_cell(r + 1, col_name, value)  # r = 0-based data idx; sheet row = r + 2
        print("  updated")

    print("\n=== next step ===")
    print("  regenerate plots/index.geojson via scripts/build_plots_geojson.py")
    print("  (or the rebuild-plots-index.yml workflow) to publish the polygon")


if __name__ == "__main__":
    main()
