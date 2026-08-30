#!/usr/bin/env python3
"""Build sunmint/trees/index.geojson from the SunMint Tree Planting sheet.

Treasury-cache pattern: data + generator + workflow live together in the
sunmint data repo. Reads the sheet via GOOGLE_SERVICE_ACCOUNT_JSON, emits
trees/index.geojson (FeatureCollection). Run by .github/workflows/rebuild-tree-index.yml.

Usage:
    GOOGLE_SERVICE_ACCOUNT_JSON=<json> python3 scripts/build_tree_geojson.py [--out trees/index.geojson]
"""

import argparse
import json
import os
import re
import sys

SHEET_ID = "1qbZZhf-_7xzmDTriaJVWj6OZshyQsFkdsAV8-pyzASQ"
SHEET_TAB = "SunMint Tree Planting"


# Photo URLs in the sheet are sometimes stored as github.com web-UI links
# (github.com/TrueSightDAO/sunmint/tree/main/... or /blob/...), which a browser
# <img> cannot render (they return HTML). Normalize to raw.githubusercontent.com.
def normalize_photo_url(url):
    if not url:
        return None
    u = url.strip()
    u = re.sub(
        r"^https?://github\.com/TrueSightDAO/sunmint/(?:tree|blob)/main/",
        "https://raw.githubusercontent.com/TrueSightDAO/sunmint/main/",
        u,
    )
    return u or None


def get_sheet():
    import gspread
    from google.oauth2 import service_account

    creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not creds_json:
        sys.exit("GOOGLE_SERVICE_ACCOUNT_JSON env var required")
    creds = service_account.Credentials.from_service_account_info(
        json.loads(creds_json),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID).worksheet(SHEET_TAB)


def idx(header, *names):
    # Column matching with needle priority + exact-before-prefix.
    # For each needle (most specific first), scan ALL headers for an EXACT
    # match, then a PREFIX match. This prevents substrings from misfiring:
    # e.g. "planted" must NOT hit "Photo of Tree Planted" (col 9) when we
    # want "Tree Planting Time" (col 17) -- the "tree planting time" needle
    # wins first. "status" must hit "Status" exactly, not "Status date".
    hl = [(h or "").strip().lower() for h in header]
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


def cell(row, i):
    try:
        v = row[i].strip() if i is not None and i < len(row) else ""
    except Exception:
        v = ""
    return v or None


def load_trees(ws):
    rows = ws.get_all_values()
    if not rows:
        return []
    header = rows[0]
    c_id = idx(header, "telegram update id", "tree id")
    c_species = idx(header, "specie", "species")
    c_lat = idx(header, "latitude")
    c_lng = idx(header, "longitude")
    c_photo = idx(header, "photo of tree planted", "photo of tree", "photo")
    c_status = idx(header, "status")
    c_qr = idx(header, "linked qr code", "linked qr", "qr code")
    c_time = idx(header, "tree planting time", "planting time", "planted at")
    c_plot = idx(header, "plot id", "plot", "parcel", "site name", "site")
    if c_id is None:
        sys.exit("could not find tree id column")
    trees = []
    for row in rows[1:]:
        if not any((v or "").strip() for v in row):
            continue
        tid = cell(row, c_id)
        if not tid:
            continue
        # Skip test / E2E rows
        tl = tid.lower()
        if "e2e" in tl or "test" in tl:
            continue
        lat = cell(row, c_lat)
        lng = cell(row, c_lng)

        def to_float(v):
            try:
                return float(v.replace(",", ".")) if v else None
            except (ValueError, AttributeError):
                return None

        status = cell(row, c_status) or "NEW"
        # Rejected/invalid trees stay in the sheet as audit history but must
        # NOT appear in the public index (governor reject flow). The monitor
        # page loads index.geojson, so an INVALID row here is exactly what
        # makes a rejected tree "reappear" on reload.
        if str(status).strip().upper() == "INVALID":
            continue
        trees.append(
            {
                "id": tid,
                "species": cell(row, c_species) or "unknown",
                "lat": to_float(lat),
                "lng": to_float(lng),
                "photo": normalize_photo_url(cell(row, c_photo)),
                "status": status,
                "qr_code": cell(row, c_qr) or None,
                "plot_id": cell(row, c_plot) or None,
                "planted_at": cell(row, c_time) or None,
                "planting_time": cell(row, c_time) or None,
            }
        )
    return trees


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="trees/index.geojson")
    args = ap.parse_args()
    ws = get_sheet()
    trees = load_trees(ws)
    features = []
    for t in trees:
        props = {
            "tree_id": t["id"],
            "species": t["species"],
            "last_measured": t["planted_at"],
            "photo_url": t["photo"],
            "status": t["status"],
            "qr_code": t["qr_code"],
            "plot_id": t.get("plot_id"),
        }
        props = {k: v for k, v in props.items() if v is not None}
        if t["lat"] is not None and t["lng"] is not None:
            geom = {"type": "Point", "coordinates": [t["lng"], t["lat"]]}
        else:
            geom = None  # no coordinates -> cannot be distance-ranked
        features.append(
            {
                "type": "Feature",
                "geometry": geom,
                "properties": props,
            }
        )
    out = {
        "type": "FeatureCollection",
        "generated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "features": features,
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"wrote {len(features)} features to {args.out}")
    # Plots layer: GeoJSON Polygon features for project parcel boundaries.
    # Populated from the SunMint Plots tab (or digitization) as boundaries are
    # defined. Empty to start -- consumers must treat a missing/empty plots
    # layer as "no plots yet" and render tree points alone.
    plots_out = {
        "type": "FeatureCollection",
        "generated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "features": [],
    }
    plots_path = os.path.join(os.path.dirname(args.out) or ".", "plots.geojson")
    with open(plots_path, "w", encoding="utf-8") as f:
        json.dump(plots_out, f, ensure_ascii=False, indent=2)
    print(f"wrote {len(plots_out['features'])} plot features to {plots_path}")


if __name__ == "__main__":
    main()
